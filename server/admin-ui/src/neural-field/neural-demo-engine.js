(function (global) {
  'use strict';

  const EMOTIONS = ['joy', 'sadness', 'excitement', 'worry', 'annoyance', 'curiosity'];
  const copy = value => JSON.parse(JSON.stringify(value));
  const clip = (value, min, max) => Math.max(min, Math.min(max, value));
  const short = (text, count) => text.length > count ? text.slice(0, count - 1) + '…' : text;
  function hash(text) {
    let value = 2166136261;
    for (let i = 0; i < text.length; i++) {
      value ^= text.charCodeAt(i);
      value = Math.imul(value, 16777619);
    }
    return (value >>> 0).toString(36);
  }
  function clockAfter(clock, minutes) {
    const parts = clock.split(':').map(Number);
    const time = (parts[0] * 60 + parts[1] + minutes) % 1440;
    return String(Math.floor(time / 60)).padStart(2, '0') + ':' + String(time % 60).padStart(2, '0');
  }

  class CognitiveDemoEngine {
    constructor() {
      this.scenarios = [
        { id: 'interview', label: '有点紧张', text: '明天要面试了，有点紧张。希望这次能顺利。', kind: 'chat', inputLabel: '用户输入' },
        { id: 'good-news', label: '分享好消息', text: '面试通过啦！终于可以松一口气了。', kind: 'chat', inputLabel: '用户输入' },
        { id: 'correction', label: '纠正一个事实', text: '我的狗不叫旺财，叫来福。这个帮我改一下。', kind: 'chat', inputLabel: '用户输入' },
        { id: 'small-talk', label: '一句闲聊', text: '嗯，哈哈。', kind: 'chat', inputLabel: '用户输入' },
        { id: 'world', label: '世界线推进', text: '雨停了。窗边的画稿终于完成，可以收起画笔休息一下。', kind: 'world', inputLabel: '角色世界 · 模拟事件' },
        { id: 'recall', label: '唤回旧记忆', text: '你还记得我的狗叫什么吗？', kind: 'chat', inputLabel: '用户输入' }
      ];
      this._appliedIds = new Set();
      this.state = {
        revision: 0,
        archive: [],
        facts: [{ id: 'seed-dog', key: 'user.pet.dog.name', value: '旺财', text: '用户的狗叫旺财', status: 'active', heat: 44, origin: 'seed', sourceLabel: '初始演示设定', sourceEventId: null }],
        emotion: { joy: 58, sadness: 12, excitement: 30, worry: 20, annoyance: 8, curiosity: 62 },
        world: { clock: '18:36', location: '窗边工作台', activity: '整理未完成的画稿', concern: '暂无待跟进事项', next: '继续画画，等待新事件', revision: 0, domain: 'simulated', day: 1 },
        episodes: [],
        retrievalsCount: 0
      };
    }

    snapshot() { return copy(this.state); }

    preview(text, kind = 'chat') {
      const input = String(text || '').trim().slice(0, 1200);
      const sourceKind = kind === 'world' ? 'world' : 'chat';
      const id = 'evt-' + this.state.revision + '-' + hash(sourceKind + ':' + input);
      const active = key => this.state.facts.find(f => f.key === key && f.status === 'active');
      const delta = Object.fromEntries(EMOTIONS.map(key => [key, 0]));
      const result = {
        id, input, kind: sourceKind, baseRevision: this.state.revision,
        simulation: true, inputLabel: sourceKind === 'world' ? '角色世界 · 模拟事件' : '用户输入',
        segments: [], facts: [], emotionDelta: delta, appraisal: '', intention: '',
        worldUpdate: {}, response: '', recall: [], episode: null, importantCount: 0,
        archivePolicy: '原始输入与回复完整归档；仅有价值的内容提炼为长期记忆。',
        shortTermDiscard: [], mode: 'conversation'
      };
      const fact = (key, value, body, origin = 'user_report') => {
        const old = active(key);
        result.facts.push({ key, value, text: body, origin, status: 'active', heat: 58, operation: old ? 'correct' : 'add', supersedes: old ? old.id : null });
      };
      const episode = (title, body, domain = 'real') => {
        result.episode = { title, text: body, domain, origin: domain === 'simulated' ? 'world_event' : 'conversation', confidence: '来源明确' };
      };

      if (!input) {
        result.mode = 'empty';
        result.appraisal = '等待一条输入。';
        result.intention = '保持当前状态';
        return result;
      }

      const asksDog = /(?:狗|狗狗|小狗).{0,8}(?:叫什么|名字|叫啥)/.test(input) && /[？?]|还记得|叫什么|叫啥/.test(input);
      const asksInterview = /面试/.test(input) && /(?:上次|之前|还记得).*(?:面试)|(?:面试).*(?:怎么样|结果|还记得)/.test(input);
      const correction = input.match(/(?:狗狗|小狗|狗)(?:的名字)?(?:不叫|不是叫|不是)\s*([^\s，,。！？]{1,12}?)\s*[，,]?\s*(?:现在|而是|应该)?(?:改叫|叫|改成)\s*([^\s，,。！？]{1,12})/);
      const dogNamed = input.match(/(?:我(?:家|的)?(?:小狗|狗狗|狗)|这只狗)(?:的名字)?(?:叫|是)\s*([^\s，,。！？]{1,12})/);

      if (sourceKind === 'world') {
        result.mode = 'world';
        const finished = /(?:画稿|画|作品).*(?:完成|画完)|(?:完成|画完).*(?:画稿|画|作品)/.test(input);
        const rainStopped = /雨停/.test(input);
        result.segments = [{ text: rainStopped ? '雨停了' : '场景发生变化', type: 'episode' }, { text: finished ? '画稿完成' : short(input, 24), type: 'episode' }, { text: '可形成分享意图', type: 'intent' }];
        delta.joy = finished ? 5 : 1; delta.excitement = finished ? 3 : 0; delta.worry = -1; delta.curiosity = rainStopped ? 2 : 1;
        result.appraisal = finished ? '一项虚构世界活动完成了。先记录进展，再决定是否分享。' : '这是一条角色世界的模拟事件，保持与用户现实事实分开。';
        result.intention = finished ? '保存完成经历；稍后再判断要不要发动态' : '记录世界变化，暂不主动发消息';
        result.worldUpdate = { activity: finished ? '收起画笔，短暂休息' : short(input, 32), next: finished ? '分享候选：完成的画稿（尚未发布）' : '等待下一件有意义的事情' };
        if (rainStopped) result.worldUpdate.location = '雨后的窗边工作台';
        result.response = finished ? '总算画完了。雨也刚好停了，先让眼睛歇一会儿。' : '把这一刻留在世界线里，先不急着说点什么。';
        episode(finished ? '画稿完成，雨停了' : '世界线新事件', input, 'simulated');
      } else if (asksDog || asksInterview) {
        result.mode = 'recall';
        const memory = active(asksDog ? 'user.pet.dog.name' : 'user.interview.status');
        result.segments = [{ text: asksDog ? '查询：狗的名字' : '查询：面试结果', type: 'intent' }, { text: '按主题召回有效记录', type: 'episode' }];
        result.recall = memory ? [copy(memory)] : [];
        delta.curiosity = 1;
        result.appraisal = memory ? '命中当前有效记忆。旧版本不作为本轮答案。' : '当前记忆里没有足够依据，不能补出一段共同经历。';
        result.intention = memory ? '引用一条相关记忆，不把整个档案放进上下文' : '坦诚说明还没有记录';
        result.response = memory ? (asksDog ? '记得，叫' + memory.value + '。' : memory.value === 'passed' ? '你告诉我面试通过了，终于松了一口气。' : '记得你提到要去面试，但我还没收到结果。') : '这件事我这里还没有确定记录，你再告诉我一次好吗？';
      } else if (correction || dogNamed) {
        result.mode = correction ? 'correction' : 'fact';
        const name = (correction ? correction[2] : dogNamed[1]).replace(/(?:啦|哦|呀|了)$/, '');
        fact('user.pet.dog.name', name, '用户的狗叫' + name);
        result.segments = [{ text: correction ? '旧信息需要作废' : '宠物信息', type: 'fact' }, { text: '狗名：' + name, type: 'fact' }, { text: '用户明确更正' , type: 'intent' }];
        delta.curiosity = 1; delta.worry = -1;
        result.appraisal = correction ? '这是同一只狗名字的明确纠正，保留修订来源并替换当前事实。' : '用户明确提供了宠物名字，可以沉淀为事实。';
        result.intention = correction ? '旧事实退出召回，新名字成为当前有效版本' : '写入宠物名字，关联这次对话';
        result.response = correction ? '好，改成' + name + '了。旧名字不再当作现在的名字。' : name + '，记住这个名字了。';
        episode(correction ? '纠正宠物名字' : '记下宠物名字', input);
      } else if (/面试/.test(input) && /通过|过了|成功|拿到.{0,5}offer|录用/.test(input) && !/没通过|未通过|没过|没有通过/.test(input)) {
        result.mode = 'good-news';
        const prior = active('user.interview.status');
        fact('user.interview.status', 'passed', '用户报告面试已通过');
        result.segments = [{ text: '面试通过', type: 'fact' }, { text: '终于松一口气', type: 'episode' }, { text: '关闭面试关注事项', type: 'intent' }];
        delta.joy = 12; delta.excitement = 9; delta.worry = -8; delta.sadness = -2;
        result.appraisal = '用户分享了一个明确的好结果，可以庆祝并结束之前的担心。';
        result.intention = '回应好消息，沉淀结果，不继续追问面试';
        result.worldUpdate = { concern: '面试已通过 · 关注事项关闭', next: '保留这段共同经历，恢复自己的活动' };
        result.response = prior && prior.value === 'upcoming' ? '过了！之前那点紧张总算可以放下了，今晚庆祝一下。' : '太好了！这下终于能好好松口气了。';
        episode('用户分享面试通过的好消息', prior ? '此前用户提到面试；本轮明确报告通过。' : '用户本轮明确报告面试通过，没有补写此前经历。');
      } else if (/面试/.test(input) && /明天|后天|马上|准备|要去|紧张|担心/.test(input)) {
        result.mode = 'concern';
        fact('user.interview.status', 'upcoming', '用户提到即将面试，当前有些紧张');
        result.segments = [{ text: /明天/.test(input) ? '明天面试' : '即将面试', type: 'fact' }, { text: '有些紧张', type: 'episode' }, { text: '合适时机关心进展', type: 'intent' }];
        delta.joy = -1; delta.worry = 5; delta.curiosity = 3; delta.excitement = -1;
        result.appraisal = '这件事对用户很重要。先回应紧张，保持关注，不立刻堆一份建议清单。';
        result.intention = '先安慰；保存未完成事项，稍后只在合适时机问一次';
        result.worldUpdate = { concern: '关注：用户即将面试，有些紧张', next: '等面试结束或用户提起，再关心进展' };
        result.response = '临到面试确实容易紧张。先缓口气，你最担心的是哪一部分？';
        episode('用户谈到即将面试的紧张', input);
      } else if (/^[嗯哦噢啊哈哈呵嘿好呀啦呢吧诶唉，。！？、,.!?\s～~]+$/.test(input) && input.length < 25) {
        result.mode = 'small-talk';
        result.segments = [{ text: input, type: 'noise' }, { text: '不形成长期事实', type: 'noise' }];
        if (/哈|嘿/.test(input)) delta.joy = 1;
        result.appraisal = '这是一个轻量回应，原文照常归档，不制造新的事实或待办。';
        result.intention = '保持自然交流；释放工作记忆，不删除原始记录';
        result.shortTermDiscard = ['低信息量短语', '重复语气词'];
        result.response = /哈|嘿/.test(input) ? '哈哈。' : '嗯，我在。';
      } else {
        result.mode = 'custom';
        const chunks = input.split(/[，。！？,!?\n]+/).map(s => s.trim()).filter(Boolean);
        result.segments = chunks.slice(0, 4).map(part => ({ text: short(part, 26), type: 'episode' }));
        if (/记住/.test(input) && /我喜欢/.test(input)) {
          const preference = input.match(/我喜欢([^，。！？,!?\n]{1,50})/);
          if (preference) fact('user.preference.' + hash(preference[1]), preference[1], '用户明确说喜欢' + preference[1]);
        }
        if (/不开心|难过|累了|好累|疲惫/.test(input)) { delta.joy = -1; delta.worry = 2; }
        else if (/开心|高兴|太棒|好消息/.test(input)) { delta.joy = 2; delta.excitement = 1; }
        else if (/担心|紧张|焦虑/.test(input)) { delta.worry = 2; delta.curiosity = 1; }
        else delta.curiosity = 1;
        result.appraisal = result.facts.length ? '识别到明确的记忆请求，保存用户陈述和来源。' : '本地规则没有识别出确定的长期事实，先保存经历，不擅自补全。';
        result.intention = result.facts.length ? '提炼明确偏好，其余信息放回原始档案' : '保持短时关注，原始内容可供后续查询';
        result.response = result.facts.length ? '好，这个偏好记下了。' : /不开心|难过|累了|好累|疲惫/.test(input) ? '听起来今天有点累，先慢一点。' : '嗯，这段我留在记录里了。';
        episode('一次新的对话', input);
      }

      result.importantCount = result.facts.length + (result.episode ? 1 : 0);
      result.factCount = result.facts.length;
      result.emotionAfter = Object.fromEntries(EMOTIONS.map(key => [key, clip(this.state.emotion[key] + delta[key], 0, 100)]));
      return result;
    }

    commit(proposal) {
      if (!proposal || !proposal.id || !proposal.input || proposal.mode === 'empty') return this.snapshot();
      if (this._appliedIds.has(proposal.id)) return this.snapshot();
      const result = proposal.baseRevision === this.state.revision ? copy(proposal) : { ...this.preview(proposal.input, proposal.kind), id: proposal.id };
      this._appliedIds.add(result.id);
      const at = this.state.world.clock;
      this.state.archive.push({ id: result.id, text: result.input, kind: result.kind, at, day: this.state.world.day, reply: result.response, origin: result.kind === 'world' ? 'simulated_world' : 'user_report', appraisal: result.appraisal, intention: result.intention });
      result.facts.forEach((candidate, index) => {
        const old = this.state.facts.find(item => item.key === candidate.key && item.status === 'active');
        const newId = result.id + '-fact-' + index;
        if (old && old.value === candidate.value) {
          old.heat = Math.min(100, old.heat + 5);
          old.lastMentionEventId = result.id;
          return;
        }
        if (old) { old.status = 'superseded'; old.supersededBy = newId; old.heat = 0; }
        this.state.facts.push({ ...candidate, id: newId, status: 'active', sourceEventId: result.id, at, supersedes: old ? old.id : null });
      });
      if (result.episode) this.state.episodes.push({ ...result.episode, id: result.id + '-episode', sourceEventId: result.id, at });
      EMOTIONS.forEach(key => { this.state.emotion[key] = clip(this.state.emotion[key] + (Number(result.emotionDelta[key]) || 0), 0, 100); });
      if (!this.externalWorld) {
        const oldClock = this.state.world.clock;
        this.state.world = { ...this.state.world, ...result.worldUpdate, clock: clockAfter(oldClock, result.kind === 'world' ? 12 : 3), revision: this.state.world.revision + 1 };
        if (this.state.world.clock < oldClock) this.state.world.day += 1;
      }
      if (result.mode === 'recall') this.state.retrievalsCount += 1;
      this.state.revision += 1;
      this.state.lastResult = result;
      return this.snapshot();
    }

    search(query) {
      const text = String(query || '').trim().toLowerCase();
      if (!text) return copy(this.state.archive.slice().reverse());
      const terms = text.split(/\s+/).filter(Boolean);
      return copy(this.state.archive.filter(entry => terms.every(term => (entry.text + ' ' + entry.reply).toLowerCase().includes(term))).reverse());
    }
  }

  global.CognitiveDemoEngine = CognitiveDemoEngine;
})(typeof window !== 'undefined' ? window : globalThis);
