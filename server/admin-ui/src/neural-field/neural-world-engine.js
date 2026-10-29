(function (global) {
  'use strict';
  const MIN = 60000, HOUR = 60 * MIN, DAY = 24 * HOUR, OFFSET = 8 * HOUR;
  const clone = value => JSON.parse(JSON.stringify(value));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const dayStart = time => Math.floor((time + OFFSET) / DAY) * DAY - OFFSET;
  const minuteOfDay = time => Math.floor((time - dayStart(time)) / MIN);
  const pad = value => String(value).padStart(2, '0');
  const clock = time => { const d = new Date(time + OFFSET); return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()); };
  const dateLabel = time => { const d = new Date(time + OFFSET); return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()); };
  const weatherDefs = {
    clear: { label: '晴朗', temperature: 23 }, cloudy: { label: '多云', temperature: 21 },
    rain: { label: '下雨', temperature: 18 }, storm: { label: '雷雨', temperature: 16 }
  };
  // A small fictional daily routine. Times are Asia/Shanghai, not locale-dependent.
  const schedule = [
    [0, 'sleep', '睡眠', '卧室'], [420, 'breakfast', '做早餐、吃早餐', '厨房'],
    [465, 'tidy', '整理房间与工作台', '房间'], [510, 'drawing-am', '专注画稿', '窗边工作台'],
    [720, 'lunch', '午饭与休息', '厨房'], [810, 'reading', '读书、整理灵感', '书桌'],
    [870, 'drawing-pm', '继续完成画稿', '窗边工作台'], [990, 'tea', '喝水、短暂休息', '房间'],
    [1010, 'finish-drawing', '整理画稿与收尾', '窗边工作台'], [1045, 'walk', '出门散步', '附近街道'],
    [1080, 'dinner', '准备晚饭', '厨房'], [1140, 'evening-reading', '读书、听音乐', '房间'],
    [1230, 'journal', '整理今天的经历', '书桌'], [1290, 'wind-down', '自由活动、准备休息', '房间'],
    [1380, 'sleep', '睡眠', '卧室']
  ].map((row, index) => ({ minute: row[0], key: row[1], title: row[2], location: row[3], index, weatherSensitive: row[1] === 'walk' }));

  class WorldlineSimulation {
    constructor({ nowMs = Date.now(), mode = 'demo', seed = 42 } = {}) {
      this._seed = Number(seed) >>> 0;
      this._initialSeed = this._seed;
      this._serial = 0; this._revision = 0; this._events = []; this._pending = [];
      this._paused = false; this._lastWall = Number(nowMs); this._adaptedSlots = new Set();
      this._concern = '没有待跟进的对话事项'; this._socialIntent = ''; this._socialUntil = 0;
      this._init(mode, Number(nowMs), true);
    }
    _random() { this._seed = (Math.imul(this._seed, 1664525) + 1013904223) >>> 0; return this._seed / 4294967296; }
    _init(mode, nowMs, initial) {
      this.mode = mode === 'realtime' ? 'realtime' : 'demo'; this.speed = this.mode === 'demo' ? 60 : 1;
      this._epoch = this.mode === 'demo' ? dayStart(nowMs) + 1030 * MIN : nowMs;
      this._lastWall = nowMs; this._seed = this._initialSeed;
      this._weather = { kind: 'cloudy', ...weatherDefs.cloudy, reason: '虚构气象初始状态；不代表现实天气', until: this._epoch + 18 * MIN };
      this._disturbance = { active: false, title: '暂无偶发事件', until: null, reason: '偶发事件有冷却，不持续打断日程' };
      this._disturbanceCooldownUntil = this._epoch; this._nextDisturbanceCheck = this._epoch + 27 * MIN;
      this._adaptedSlots.clear();
      this._activity = this._resolveActivity(this._epoch);
      if (!initial) { this._events = []; this._pending = []; }
      this._emit('world', initial ? '世界线开始推进' : '重新对齐世界时间', this.mode === 'demo' ? '加速演示从 17:10 开始，现实 1 秒对应模拟 1 分钟' : '时钟与当前时间同步；天气仍为模拟', this._activity.title, '根据时间、天气和日程形成当前处境');
    }
    _emit(type, summary, cause, effect, decision, affectDelta = {}) {
      const event = { id: 'world-' + (++this._serial), epochMs: this._epoch, clock: clock(this._epoch), type, summary, cause, effect, decision, affectDelta: { ...affectDelta } };
      this._events.push(event); this._pending.push(event); this._revision++;
      if (this._events.length > 12) this._events.splice(0, this._events.length - 12);
      // Bound unattended notification backlog while retaining a useful causal tail.
      if (this._pending.length > 80) this._pending.splice(0, this._pending.length - 80);
      return event;
    }
    _slotAt(time) {
      const day = dayStart(time), minute = minuteOfDay(time);
      if (minute < 420) return { ...schedule[0], startedAt: day - DAY + 1380 * MIN, endsAt: day + 420 * MIN, slotId: (day - DAY) + '-sleep' };
      let slot = schedule[0];
      for (const item of schedule) { if (item.minute <= minute) slot = item; else break; }
      const next = schedule[slot.index + 1];
      return { ...slot, startedAt: day + slot.minute * MIN, endsAt: next ? day + next.minute * MIN : day + DAY + 420 * MIN, slotId: day + '-' + slot.key };
    }
    _resolveActivity(time) {
      const slot = this._slotAt(time);
      if (this._disturbance.active && time < this._disturbance.until) {
        return { id: this._disturbance.id, title: this._disturbance.action, location: this._disturbance.location, status: 'interrupted', startedAt: this._disturbance.startedAt, endsAt: this._disturbance.until, reason: this._disturbance.reason, slotId: slot.slotId, weatherSensitive: slot.weatherSensitive };
      }
      const badWeather = ['rain', 'storm'].includes(this._weather.kind);
      if (slot.weatherSensitive && badWeather) {
        return { id: slot.slotId + '-indoor', title: '改为室内拉伸、休息', location: '房间', status: 'adapted', startedAt: slot.startedAt, endsAt: slot.endsAt, reason: this._weather.kind === 'storm' ? '雷雨影响户外活动，暂时取消散步' : '下雨与散步计划冲突，先在室内活动', slotId: slot.slotId, weatherSensitive: true };
      }
      return { id: slot.slotId, title: slot.title, location: slot.location, status: 'running', startedAt: slot.startedAt, endsAt: slot.endsAt, reason: slot.key === 'sleep' ? '进入固定休息时段，不主动安排新任务' : slot.weatherSensitive ? '处于散步时段，当前天气适合外出' : '按当前时间执行日程，天气无需改变这项室内活动', slotId: slot.slotId, weatherSensitive: slot.weatherSensitive };
    }
    _refreshActivity(cause) {
      const next = this._resolveActivity(this._epoch), previous = this._activity;
      if (previous && previous.id === next.id) { this._activity = { ...next, startedAt: previous.startedAt }; return; }
      next.startedAt = Math.max(next.startedAt, this._epoch);
      this._activity = next;
      let delta = {};
      if (next.status === 'adapted' && !this._adaptedSlots.has(next.slotId)) { this._adaptedSlots.add(next.slotId); delta = { worry: 1, joy: -1 }; }
      if (previous && previous.status === 'adapted' && next.weatherSensitive && next.status === 'running') delta = { joy: 1, worry: -1 };
      this._emit('activity', '活动更新：' + next.title, cause || next.reason, previous ? previous.title + ' → ' + next.title : next.title, next.reason, delta);
    }
    _nextWeather() {
      const previous = this._weather.kind, value = this._random();
      const transitions = {
        clear: value < .35 ? 'clear' : 'cloudy',
        cloudy: value < .2 ? 'clear' : value < .86 ? 'rain' : 'storm',
        rain: value < .6 ? 'cloudy' : value < .9 ? 'rain' : 'storm',
        storm: value < .85 ? 'rain' : 'cloudy'
      };
      const next = transitions[previous], duration = (15 + Math.floor(this._random() * 31)) * MIN;
      this._weather = { kind: next, ...weatherDefs[next], until: this._epoch + duration, reason: '模拟气象驻留结束，按相邻天气状态转移' };
      if (next !== previous) this._emit('weather', weatherDefs[previous].label + ' → ' + weatherDefs[next].label, '气象状态持续一段时间后发生变化', this._slotAt(this._epoch).weatherSensitive ? '重新检查户外活动是否可执行' : '当前为室内活动，保留原日程', '仅在天气影响活动时调整计划，不因下雨自动悲伤');
    }
    _startDisturbance(manual) {
      if (this._disturbance.active || this._epoch < this._disturbanceCooldownUntil) return false;
      const slot = this._slotAt(this._epoch);
      if (slot.key === 'sleep') {
        if (manual) this._emit('disturbance', '夜间保持休息', '有轻微外界声响，但未达到需要起身处理的程度', '睡眠继续', '不因每个随机信号打断睡眠');
        return false;
      }
      const outdoors = this._activity.location === '附近街道';
      const choices = outdoors ? [
        { title: '前方道路临时施工', action: '绕开施工路段', location: '附近街角', minutes: 5, reason: '保持散步目标，临时调整路径' },
        { title: '鞋带松了', action: '停下来整理鞋带', location: '街边', minutes: 2, reason: '处理短暂障碍后继续原活动' }
      ] : [
        { title: '门铃响了', action: '到门口取包裹', location: '门口', minutes: 4, reason: '处理一次短暂来访，再回到当前日程' },
        { title: '水杯空了', action: '起身接杯水', location: '厨房', minutes: 3, reason: '补充饮水，暂时打断手上的事情' },
        { title: '久坐有些僵硬', action: '起身舒展一下', location: '房间', minutes: 5, reason: '短暂活动身体后恢复原安排' }
      ];
      const picked = choices[Math.floor(this._random() * choices.length)];
      this._disturbance = { active: true, id: 'disturbance-' + (this._serial + 1), title: picked.title, action: picked.action, location: picked.location, startedAt: this._epoch, until: this._epoch + picked.minutes * MIN, reason: picked.reason };
      this._disturbanceCooldownUntil = this._epoch + 45 * MIN;
      this._emit('disturbance', picked.title, manual ? '手动触发一次模拟扰动' : '带冷却的随机事件触发', picked.action, picked.reason, picked.title === '门铃响了' ? { curiosity: 1 } : {});
      return true;
    }
    _advance(target) {
      if (!(target > this._epoch)) return;
      // On a very large jump, skip unobserved micro-events instead of pretending
      // to have executed days of activity. Resolve the actual final time slot.
      if (target - this._epoch > 12 * HOUR) {
        const from = this._epoch;
        this._epoch = target;
        this._disturbance = { active: false, title: '暂无偶发事件', until: null, reason: '长时间离开后不补发过期扰动' };
        this._disturbanceCooldownUntil = target; this._nextDisturbanceCheck = target + 30 * MIN;
        this._weather.until = target + 20 * MIN;
        this._weather.reason = '时间跨度较大，沿用最后可知的模拟气象，再从当前时刻推进';
        this._activity = this._resolveActivity(target);
        this._emit('world', '世界线已追到当前时刻', '经过约 ' + Math.floor((target - from) / HOUR) + ' 小时；不补演离线期间的细碎事件', this._activity.title, '按当前时段恢复，历史空白不写成已完成经历');
        return;
      }
      let guard = 0;
      while (this._epoch < target && guard++ < 512) {
        const slot = this._slotAt(this._epoch);
        const candidates = [target, slot.endsAt, this._weather.until, this._nextDisturbanceCheck];
        if (this._disturbance.active) candidates.push(this._disturbance.until);
        const next = Math.min(...candidates.filter(value => value > this._epoch));
        if (!Number.isFinite(next)) break;
        this._epoch = next;
        let cause = '到达日程中的下一个时间点';
        if (this._epoch >= this._weather.until) { this._nextWeather(); cause = '天气状态发生变化，重新评估当前活动'; }
        if (this._disturbance.active && this._epoch >= this._disturbance.until) {
          const title = this._disturbance.title;
          this._disturbance = { active: false, title: '暂无偶发事件', until: null, reason: '短暂扰动已结束，恢复当前时段的安排' };
          this._emit('disturbance', '临时事项已处理', title, '返回当前有效日程', '依据现在的时间恢复，不回到已经过期的旧任务');
          cause = '短暂扰动结束';
        }
        if (this._epoch >= this._nextDisturbanceCheck) {
          this._nextDisturbanceCheck = this._epoch + (20 + Math.floor(this._random() * 21)) * MIN;
          if (this._random() < .28 && this._startDisturbance(false)) cause = '偶发事件需要短暂处理';
        }
        this._refreshActivity(cause);
      }
      if (this._epoch < target) { this._epoch = target; this._refreshActivity('时间追赶后按当前处境重新对齐'); }
    }
    tick(nowMs = Date.now()) {
      const wall = Number(nowMs), elapsed = Math.max(0, wall - this._lastWall);
      this._lastWall = wall;
      if (!this._paused) this._advance(this.mode === 'realtime' ? Math.max(this._epoch, wall) : this._epoch + elapsed * this.speed);
      return this.snapshot();
    }
    setMode(mode, nowMs = Date.now()) { this._init(mode, Number(nowMs), false); return this.snapshot(); }
    setPaused(value, nowMs = Date.now()) {
      const next = !!value;
      if (next && !this._paused) this.tick(nowMs);
      this._paused = next; this._lastWall = Number(nowMs);
      return this.snapshot();
    }
    advanceMinutes(minutes) {
      const amount = clamp(Number(minutes) || 0, 0, 24 * 60);
      if (amount > 0 && this.mode === 'realtime') {
        this.mode = 'demo'; this.speed = 60;
        this._emit('world', '进入时间推进演示', '手动跳转时间后不再标成实时状态', '从当前世界时间继续以 60 倍速度推进', '可随时切回跟随当前时间');
      }
      this._advance(this._epoch + amount * MIN); return this.snapshot();
    }
    setWeather(kind) {
      if (!weatherDefs[kind]) return this.snapshot();
      const prior = this._weather.kind;
      this._weather = { kind, ...weatherDefs[kind], until: this._epoch + 30 * MIN, reason: '手动设定的模拟天气，至少保持 30 分钟' };
      if (kind !== prior) this._emit('weather', weatherDefs[prior].label + ' → ' + weatherDefs[kind].label, '修改模拟气象输入', '重新评估当前活动的环境条件', this._slotAt(this._epoch).weatherSensitive ? '户外活动需要适配天气' : '室内活动不因天气变化而中断');
      this._refreshActivity('天气输入更新');
      return this.snapshot();
    }
    perturb() { if (this._startDisturbance(true)) this._refreshActivity('新扰动短暂介入当前活动'); return this.snapshot(); }
    noteConversation(result) {
      const value = result && result.worldUpdate && result.worldUpdate.concern;
      if (typeof value !== 'string' || !value.trim() || value === this._concern) return this.snapshot();
      this._concern = value.trim().slice(0, 180);
      this._socialIntent = String(result.intention || '').slice(0, 180); this._socialUntil = this._epoch + 30 * MIN;
      this._emit('conversation', '关注事项更新', '来自本轮对话的明确线索', this._concern, '将对话纳入当前处境；时钟与日程继续独立推进');
      return this.snapshot();
    }
    drainEvents() { const output = clone(this._pending); this._pending = []; return output; }
    snapshot() {
      const slot = this._slotAt(this._epoch), active = this._activity, minute = minuteOfDay(this._epoch);
      const progress = clamp((this._epoch - active.startedAt) / Math.max(MIN, active.endsAt - active.startedAt), 0, 1);
      const plan = schedule.map((item, index) => {
        const end = schedule[index + 1] ? schedule[index + 1].minute : 1440;
        const current = minute >= item.minute && minute < end;
        return { id: dayStart(this._epoch) + '-' + item.key + '-' + index, time: pad(Math.floor(item.minute / 60)) + ':' + pad(item.minute % 60), title: item.title, status: current ? active.status === 'adapted' ? 'adjusted' : 'current' : minute >= end ? 'done' : 'upcoming', weatherSensitive: item.weatherSensitive };
      });
      let intent = active.status === 'interrupted' ? '处理完这件小事，再按当前时间恢复日程' : active.status === 'adapted' ? '先完成室内替代活动；天气转好且仍有时间时再外出' : slot.key === 'sleep' ? '保持安静休息，等早晨再安排新的活动' : '继续' + active.title + '，到 ' + clock(active.endsAt) + ' 再切换安排';
      if (this._socialIntent && this._epoch < this._socialUntil) intent += '；' + this._socialIntent;
      return clone({
        revision: this._revision, epochMs: this._epoch, clock: clock(this._epoch), dateLabel: dateLabel(this._epoch), mode: this.mode, speed: this.speed,
        weather: this._weather,
        activity: { id: active.id, title: active.title, location: active.location, status: active.status, startedAt: active.startedAt, endsAt: active.endsAt, progress, reason: active.reason },
        plan, disturbance: this._disturbance, concern: this._concern, intent,
        summary: clock(this._epoch) + ' · ' + this._weather.label + ' · ' + active.location + ' · ' + active.title,
        nextAt: Math.min(active.endsAt, this._weather.until, this._nextDisturbanceCheck), events: this._events
      });
    }
  }
  global.WorldlineSimulation = WorldlineSimulation;
})(typeof window !== 'undefined' ? window : globalThis);
