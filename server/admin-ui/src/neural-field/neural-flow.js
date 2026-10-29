(function () {
  'use strict';
  const TAU = Math.PI * 2;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const short = (value, n) => { const a = Array.from(String(value || '')); return a.length > n ? a.slice(0, n).join('') + '…' : a.join(''); };
  const initialStatus = { archive: '等待新记录', memory: '保留有效事实', affect: '保持平稳', world: '延续当前处境', intent: '等待下一件事', attention: '没有占用' };
  function affectPhrase(delta) {
    const d = delta || {};
    if (Number(d.worry) < 0 && (Number(d.joy) > 0 || Number(d.worry) <= -3)) return '松了一口气';
    const strongest = Object.entries(d).filter(([, value]) => Number.isFinite(Number(value)) && Number(value) !== 0).sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])))[0];
    if (!strongest) return '保持平稳';
    const [key, raw] = strongest, value = Number(raw);
    const positive = { joy: '心情轻快了些', sadness: '有些低落', excitement: '有些期待', worry: '稍感担心', annoyance: '有些不悦', curiosity: '多了一点好奇' };
    const negative = { joy: '心情稍显沉重', sadness: '慢慢放松下来', excitement: '逐渐安定下来', worry: '松了一口气', annoyance: '不悦正在缓和', curiosity: '注意逐渐转移' };
    return (value > 0 ? positive : negative)[key] || '留意当前感受';
  }
  function valueText(value) {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number') return String(value);
    return value.label || value.summary || value.text || value.description || value.name || '';
  }
  const defs = [
    ['archive', '原始档案', '完整保留 · 可回查', .68, .20, [98, 181, 255]],
    ['memory', '事实沉淀', '提炼 · 纠错 · 加温', .88, .20, [116, 240, 195]],
    ['affect', '情绪变化', '短时波动 · 慢慢复原', .68, .47, [255, 187, 128]],
    ['world', '世界线', '经历延续 · 状态更新', .88, .47, [184, 164, 255]],
    ['intent', '行动意图', '想做什么 · 是否行动', .68, .74, [116, 222, 247]],
    ['attention', '短时注意', '不重要的 · 逐渐散去', .88, .74, [147, 171, 189]]
  ];
  function seeded(seed) { return () => { seed = (Math.imul(seed, 1664525) + 1013904223) | 0; return (seed >>> 0) / 4294967296; }; }

  class NeuralFlow {
    constructor(canvas) {
      if (!canvas || !canvas.getContext) throw new TypeError('NeuralFlow requires a canvas');
      this.canvas = canvas; this.ctx = canvas.getContext('2d', { alpha: true });
      this.clock = 0; this.last = 0; this.raf = 0; this.paused = false; this.dead = false;
      this.phase = 'idle'; this.phaseAt = 0; this.event = null; this.eventId = 0;
      this.flights = []; this.inputAt = -100000; this.inputChars = []; this.archiveRecords = [];
      this.worldSequence = 0; this.worldReceived = new Set();
      this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.nodes = {}; this.received = new Set(); this.width = 1; this.height = 1;
      defs.forEach((def, index) => {
        const rng = seeded(499 + index * 111), points = [];
        for (let i = 0; i < 180; i++) {
          const y = 1 - 2 * ((i + .5) / 180), rr = Math.sqrt(1 - y * y), a = i * 2.39996323;
          const r = .58 + .42 * Math.pow(rng(), .27);
          let x = Math.cos(a) * rr * r, z = Math.sin(a) * rr * r, yy = y * r;
          if (index === 0) { x *= .9; yy *= .77; z *= .7; }
          if (index === 1) { x *= 1 + .12 * Math.sin(y * 8); yy *= .9; }
          if (index === 2) { x *= 1 + .18 * Math.sin(a * 3); yy *= .85; }
          if (index === 3) { x *= 1.05; yy *= .78; z *= .7; }
          if (index === 4) { x *= .86; yy *= 1.05; }
          points.push({ x, y: yy, z, seed: rng(), radius: .65 + rng() * .65 });
        }
        points.sort((a, b) => a.seed - b.seed);
        this.nodes[def[0]] = { key: def[0], name: def[1], subtitle: def[2], status: initialStatus[def[0]], x: def[3], y: def[4], desktopX: def[3], desktopY: def[4], color: def[5], points, storageCount: 0, density: 110, pulseAt: -100000, flash: '', flashAt: -100000, energy: 0, index };
      });
      this._tick = this._tick.bind(this);
      this._observer = new ResizeObserver(() => this._resize()); this._observer.observe(canvas);
      if (!canvas.hasAttribute('aria-label')) canvas.setAttribute('aria-label', '输入文字进入脑图，分流至原始档案、事实、情绪、世界线、行动意图与短时注意的动画');
      this._resize();
      if (!this.reduced) this.raf = requestAnimationFrame(this._tick);
    }
    _resize() {
      if (this.dead) return;
      const rect = this.canvas.getBoundingClientRect(); this.width = Math.max(1, rect.width); this.height = Math.max(1, rect.height);
      this.mobile = this.width < 620;
      Object.values(this.nodes).forEach(node => {
        node.x = this.mobile ? [.17, .50, .83][node.index % 3] : node.desktopX;
        node.y = this.mobile ? (node.index < 3 ? .58 : .81) : node.desktopY;
      });
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.round(this.width * dpr); this.canvas.height = Math.round(this.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); this._render();
    }
    setEvent(result) {
      this.event = result || {}; this.eventId++; this.received.clear();
      this.inputChars = Array.from(String(this.event.input || ''));
      this.inputAt = -100000; this._render();
    }
    setSemanticState(state) {
      const patch = state || {};
      Object.keys(this.nodes).forEach(key => {
        const text = patch[key + 'Text'];
        if (typeof text === 'string' && text.trim()) this.nodes[key].status = text.trim();
      });
      this._render();
    }
    ingestWorld(event, snapshot) {
      // Environment input has its own identity and never replaces a user turn.
      const e = event || {}, snap = snapshot || {}, world = snap.world || snap;
      const explicit = valueText(e.contextText || e.situation || e.summary || e.text || e.input);
      const combined = [world.clock, world.weather, world.location, world.activity].map(valueText).filter(Boolean).join(' · ');
      const situation = explicit || combined;
      if (!situation) return;
      const sourceId = e.id || e.eventId;
      if (sourceId && this.worldReceived.has(sourceId)) return;
      if (sourceId) this.worldReceived.add(sourceId);
      if (this.worldReceived.size > 200) this.worldReceived.delete(this.worldReceived.values().next().value);
      const sequence = ++this.worldSequence;
      this.nodes.world.status = situation;
      this.nodes.world.pulseAt = this.clock;
      const affect = valueText(e.affectText || snap.affectText) || affectPhrase(e.emotionDelta);
      const intent = valueText(e.intentText || e.intention || snap.intentText || world.next) || '评估当前处境';
      this._emit('world', short(situation, 26), { reverse: true, namespace: 'world-' + sequence, worldSequence: sequence, statusText: situation });
      this._emit('affect', short(affect, 18), { delay: 1850, namespace: 'world-' + sequence, worldSequence: sequence, statusText: affect });
      this._emit('intent', short(intent, 23), { delay: 2150, namespace: 'world-' + sequence, worldSequence: sequence, statusText: intent });
      this._render();
    }
    setPhase(phase) {
      this.phase = phase || 'idle'; this.phaseAt = this.clock;
      const e = this.event || {}, segments = Array.isArray(e.segments) ? e.segments : [];
      if (phase === 'input') { this.inputAt = this.clock; this.nodes.attention.status = '留意这段输入'; this.nodes.intent.status = '正在听'; }
      if (phase === 'parse') this.nodes.intent.status = '正在理解';
      if (phase === 'idle') this.nodes.intent.status = '等待下一件事';
      if (phase === 'archive') {
        if (e.input) this._emit('archive', '原文：' + short(e.input, 20), { archive: true, full: String(e.input), addsStorage: true, statusText: '原文已保留 · 可回查' });
      }
      if (phase === 'memory') {
        const facts = Array.isArray(e.facts) ? e.facts.filter(f => typeof f === 'string' || f && (f.text || f.body || f.content)) : [];
        facts.slice(0, 3).forEach((f, i) => {
          const text = typeof f === 'string' ? f : f.text || f.body || f.content;
          const correction = f.operation === 'correct' || f.operation === 'update';
          this._emit('memory', short(text, 18), { delay: i * 280, addsStorage: !correction, statusText: correction ? '旧事实已纠正' : '有效事实已沉淀' });
        });
        if (!facts.length) this.nodes.memory.status = '本轮没有新增事实';
        const transient = segments.filter(s => s.type === 'noise');
        transient.slice(0, 2).forEach((s, i) => this._emit('attention', short(s.text, 17), { delay: i * 250, transient: true, statusText: '轻量信息 · 稍后释放' }));
        if (!facts.length && !transient.length) {
          const sample = segments.find(s => s.type === 'episode');
          if (sample) this._emit('attention', short(sample.text, 18), { transient: true, statusText: '暂时留意 · 不作事实' });
        }
      }
      if (phase === 'recall') {
        const recall = Array.isArray(e.recall) ? e.recall[0] : null;
        const text = recall && (recall.body || recall.text || recall.content);
        if (text) this._emit('memory', short(text, 20), { reverse: true, statusText: '已找到相关记忆' });
        else this.nodes.memory.status = '未找到确定记录';
      }
      if (phase === 'affect') {
        const text = valueText(e.affectText) || affectPhrase(e.emotionDelta);
        this._emit('affect', text, { statusText: text });
      }
      if (phase === 'world') {
        const updates = Object.entries(e.worldUpdate || {}).map(([, value]) => valueText(value)).filter(Boolean);
        updates.slice(0, 2).forEach((text, i) => this._emit('world', short(text, 19), { delay: i * 270, statusText: text }));
        if (e.intention) this._emit('intent', short(e.intention, 22), { delay: 380, statusText: /等待|稍后|先不|暂不/.test(e.intention) ? '等待合适时机' : '正在考虑下一步' });
      }
      if (phase === 'respond') {
        if (e.response) this._emit('intent', '正在回应这段话', { delay: 100, response: true, statusText: '正在回应' });
      }
      this._render();
    }
    _emit(key, text, options) {
      if (!text || !this.nodes[key]) return;
      const o = options || {}, id = (o.namespace || this.eventId) + ':' + key + ':' + text + ':' + !!o.reverse;
      if (this.received.has(id)) return; this.received.add(id);
      const flight = { key, text, at: this.clock + (o.delay || 0), duration: 1700, arrived: false, options: o, seed: this.flights.length % 3, eventId: this.eventId };
      this.flights.push(flight);
      if (this.reduced) this._arrive(flight);
    }
    _arrive(flight) {
      if (flight.arrived) return; flight.arrived = true;
      const node = this.nodes[flight.key];
      if ((node.key === 'archive' || node.key === 'memory') && flight.options.addsStorage && !flight.options.reverse) node.storageCount++;
      const latestWorld = !flight.options.worldSequence || flight.options.worldSequence === this.worldSequence;
      if (flight.options.statusText && latestWorld) node.status = flight.options.statusText;
      node.pulseAt = this.clock; node.flashAt = this.clock; node.flash = flight.text; node.energy = Math.min(1, node.energy + .35);
      if (flight.options.archive) this.archiveRecords.push({ eventId: flight.eventId, text: flight.options.full });
    }
    setPaused(value) {
      this.paused = !!value;
      if (this.paused) { cancelAnimationFrame(this.raf); this.raf = 0; }
      else if (!this.dead && !this.reduced && !this.raf) { this.last = 0; this.raf = requestAnimationFrame(this._tick); }
    }
    destroy() { this.dead = true; cancelAnimationFrame(this.raf); this.raf = 0; this._observer.disconnect(); }
    _tick(now) {
      this.raf = 0; if (this.dead || this.paused) return;
      if (this.last) this.clock += Math.min(60, now - this.last);
      this.last = now; this._render(); this.raf = requestAnimationFrame(this._tick);
    }
    _path(node, reverse) {
      const w = this.width, h = this.height;
      const laneOffset = (Math.floor(node.index / 2) - 1) * .065;
      const p0 = this.mobile ? { x: w * .50, y: h * .28 } : { x: w * .435, y: h * (.432 + laneOffset) }, p3 = { x: node.x * w, y: node.y * h };
      const p1 = this.mobile ? { x: w * .50, y: h * .42 } : { x: w * .535, y: h * (.432 + laneOffset) };
      const p2 = this.mobile ? { x: p3.x, y: p3.y - h * .09 } : { x: p3.x - w * .075, y: p3.y };
      return reverse ? [p3, p2, p1, p0] : [p0, p1, p2, p3];
    }
    _bezier(points, t) {
      const u = 1 - t, [a, b, c, d] = points;
      return { x: u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x, y: u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t * t * t * d.y };
    }
    _pill(text, x, y, color, alpha, maxWidth, fontSize) {
      const c = this.ctx, size = fontSize || 12;
      c.save(); c.globalAlpha = alpha;
      c.font = '500 ' + size + 'px ui-monospace, SFMono-Regular, "Microsoft YaHei", sans-serif';
      let label = String(text); const max = maxWidth || this.width * .29;
      while (c.measureText(label).width > max && label.length > 2) label = label.slice(0, -2) + '…';
      const tw = c.measureText(label).width, bw = tw + 18, bh = size + 15;
      x = clamp(x, bw / 2 + 4, this.width - bw / 2 - 4);
      const left = x - bw / 2, top = y - bh / 2;
      c.fillStyle = 'rgba(7,18,30,.94)'; c.strokeStyle = 'rgba(' + color + ',.5)'; c.lineWidth = .75;
      c.beginPath(); c.roundRect(left, top, bw, bh, 5); c.fill(); c.stroke();
      c.fillStyle = 'rgb(' + color + ')'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(label, x, y + .5); c.restore();
    }
    _render() {
      if (this.dead || this.width < 2 || this.height < 2) return;
      const c = this.ctx, w = this.width, h = this.height, time = this.clock / 1000;
      c.clearRect(0, 0, w, h);
      const radius = this.mobile ? Math.max(22, Math.min(w * .073, h * .055)) : Math.max(20, Math.min(w * .049, h * .09));
      // Readable paths sit behind particles and moving word capsules.
      Object.values(this.nodes).forEach(node => {
        const points = this._path(node), [a, b, d, e] = points;
        const rgb = node.color.join(','); c.strokeStyle = 'rgba(' + rgb + ',.13)'; c.lineWidth = .75;
        c.beginPath(); c.moveTo(a.x, a.y); c.bezierCurveTo(b.x, b.y, d.x, d.y, e.x, e.y); c.stroke();
        const dot = this._bezier(points, (time * .1 + node.index * .14) % 1);
        c.fillStyle = 'rgba(' + rgb + ',.35)'; c.beginPath(); c.arc(dot.x, dot.y, 1.3, 0, TAU); c.fill();
      });
      // Source rail. Characters, rather than a moving generic glow, enter the brain.
      c.strokeStyle = 'rgba(115,218,242,.2)'; c.lineWidth = .8;
      c.beginPath();
      if (this.mobile) { c.moveTo(w * .06, h * .10); c.quadraticCurveTo(w * .37, h * .10, w * .50, h * .28); }
      else { c.moveTo(w * .015, h * .432); c.lineTo(w * .32, h * .432); }
      c.stroke();
      c.font = '11px ui-monospace, sans-serif'; c.fillStyle = 'rgba(124,178,197,.72)'; c.textAlign = 'left';
      c.fillText('文字输入', w * (this.mobile ? .06 : .022), h * (this.mobile ? .10 : .432) - 28);
      this._drawInput();
      if (this.phase === 'parse') this._drawParse();
      // Durable stores grow slowly. Other volumes represent a current state,
      // never an accumulated number of emotions or intentions.
      Object.values(this.nodes).forEach(node => {
        const x = node.x * w, y = node.y * h, rgb = node.color.join(',');
        const age = (this.clock - node.pulseAt) / 1000, pulse = Math.max(0, 1 - age / 1.8);
        const attentionFade = node.key === 'attention' ? Math.max(.12, 1 - Math.max(0, age - 1) / 7) : 1;
        const durable = node.key === 'archive' || node.key === 'memory';
        const targetDensity = durable ? Math.min(170, 110 + node.storageCount * 2) : node.key === 'intent' ? 104 : 120;
        node.density += (targetDensity - node.density) * (this.reduced ? 1 : .025);
        const count = Math.round(node.density);
        const breath = node.key === 'affect' && !this.reduced ? 1 + Math.sin(time * .85) * (.025 + pulse * .035) : 1;
        const halo = c.createRadialGradient(x, y, 0, x, y, radius * 1.45);
        halo.addColorStop(0, 'rgba(' + rgb + ',' + (.055 + pulse * .13).toFixed(3) + ')'); halo.addColorStop(1, 'rgba(' + rgb + ',0)');
        c.fillStyle = halo; c.fillRect(x - radius * 1.45, y - radius * 1.45, radius * 2.9, radius * 2.9);
        const rotation = this.reduced ? .3 : time * .11 + node.index * .5, cs = Math.cos(rotation), sn = Math.sin(rotation);
        const order = node.points.slice(0, count).map(p => ({ p, xx: p.x * cs + p.z * sn, zz: p.z * cs - p.x * sn })).sort((a, b) => b.zz - a.zz);
        for (const item of order) {
          const p = item.p, persp = 3.2 / (3.2 + item.zz), front = clamp((1 - item.zz) / 2, .05, 1);
          let scatter = 1;
          if (node.key === 'attention' && age > 2 && p.seed > .4) scatter += Math.min(1.15, (age - 2) * .12);
          const px = x + item.xx * radius * persp * scatter * breath, py = y - p.y * radius * persp * scatter * breath;
          let alpha = (.36 + front * .65 + pulse * .18) * (.89 + .11 * Math.sin(time * 1.4 + p.seed * 23));
          if (node.key === 'attention' && p.seed > .4) alpha *= attentionFade;
          c.fillStyle = 'rgba(' + rgb + ',' + clamp(alpha, .02, 1).toFixed(3) + ')';
          c.beginPath(); c.arc(px, py, p.radius * (.85 + pulse * .4), 0, TAU); c.fill();
          if (pulse > .3 && p.seed > .96) { c.strokeStyle = 'rgba(' + rgb + ',' + (pulse * .3).toFixed(3) + ')'; c.beginPath(); c.arc(px, py, 3 + pulse * 3, 0, TAU); c.stroke(); }
        }
        if (pulse > 0) { c.strokeStyle = 'rgba(' + rgb + ',' + (pulse * .23).toFixed(3) + ')'; c.lineWidth = .7; c.beginPath(); c.ellipse(x, y, radius * (1.15 + age * .25), radius * (.72 + age * .17), -.2, 0, TAU); c.stroke(); }
        c.textAlign = 'center'; c.textBaseline = 'alphabetic';
        c.font = '600 ' + (w < 650 ? 11 : 13) + 'px "Microsoft YaHei", system-ui, sans-serif';
        c.fillStyle = 'rgba(' + rgb + ',.94)'; c.fillText(node.name, x, y + radius + 19);
        c.font = '11px "Microsoft YaHei", system-ui, sans-serif'; c.fillStyle = 'rgba(174,201,215,.82)';
        let status = node.key === 'attention' && node.pulseAt >= 0 && age > 8 ? '已释放短时注意' : node.status;
        const statusWidth = w * (this.mobile ? .285 : .185);
        while (c.measureText(status).width > statusWidth && status.length > 2) status = status.slice(0, -2) + '…';
        c.fillText(status, x, y + radius + 35);
      });
      for (const flight of this.flights) {
        const progress = (this.clock - flight.at) / flight.duration;
        if (progress < 0) continue;
        if (progress >= 1) this._arrive(flight);
        if (progress > 1.33 || this.reduced) continue;
        const node = this.nodes[flight.key], path = this._path(node, flight.options.reverse);
        const q = clamp(progress, 0, 1), ease = q * q * (3 - 2 * q), p = this._bezier(path, ease);
        const alpha = Math.min(1, progress * 8) * (progress > 1 ? Math.max(0, 1 - (progress - 1) / .33) : 1);
        this._pill(flight.text, p.x, p.y - 14 - flight.seed * 4, node.color.join(','), alpha, this.mobile ? w * .40 : w * .29, w < 650 ? 11 : 13);
      }
      this.flights = this.flights.filter(f => this.clock - f.at < f.duration * 1.4);
      // Keep the most recent received phrase briefly readable at its destination.
      Object.values(this.nodes).forEach(node => {
        const age = this.clock - node.flashAt;
        if (age >= 160 && age < 3000) this._pill(short(node.flash, this.mobile ? 8 : 15), node.x * w, node.y * h - radius - 12, node.color.join(','), Math.min(1, (3000 - age) / 450), w * (this.mobile ? .26 : .18), 11);
      });
    }
    _drawInput() {
      const c = this.ctx, w = this.width, h = this.height, n = this.inputChars.length;
      if (!n) return;
      const age = this.clock - this.inputAt;
      if (age < 0 || age > 2650) return;
      const label = short(this.inputChars.join(''), this.mobile ? 19 : 25);
      const origin = this.mobile ? { x: w * .31, y: h * .10 } : { x: w * .18, y: h * .432 };
      const target = this.mobile ? { x: w * .50, y: h * .25 } : { x: w * .365, y: h * .432 };
      const t = clamp(age / 1600, 0, 1), ease = t * t * (3 - 2 * t);
      const x = origin.x + (target.x - origin.x) * ease;
      const y = origin.y + (target.y - origin.y) * ease - Math.sin(t * Math.PI) * 10;
      if (age < 1850 || this.reduced) {
        const alpha = age < 1600 ? Math.min(1, age / 180) : Math.max(0, 1 - (age - 1600) / 250);
        this._pill(label, x, y, '174,246,255', this.reduced ? 1 : alpha, w * (this.mobile ? .70 : .43), this.mobile ? 14 : 15);
      }
      // Once the complete sentence arrives, readable glyphs separate and sink
      // into the brain; the original input is preserved in the archive event.
      if (age >= 1600 && !this.reduced) {
        const chars = Array.from(label), size = this.mobile ? 14 : 15;
        c.font = '600 ' + size + 'px "Microsoft YaHei", system-ui, sans-serif';
        c.textAlign = 'center'; c.textBaseline = 'middle';
        chars.forEach((char, i) => {
          const u = clamp((age - 1600 - i * 8) / 750, 0, 1), pull = u * u;
          const initialX = target.x + (i - (chars.length - 1) / 2) * size;
          const sinkX = w * (this.mobile ? .50 : .389), sinkY = h * (this.mobile ? .28 : .432);
          const px = initialX + (sinkX - initialX) * pull;
          const py = target.y + (sinkY - target.y) * pull + Math.sin(u * Math.PI) * Math.sin(i * .9) * 20;
          const alpha = Math.max(0, 1 - u) * .92;
          c.fillStyle = 'rgba(174,246,255,' + alpha.toFixed(3) + ')';
          if (u < .8) c.fillText(char, px, py);
          else { c.beginPath(); c.arc(px, py, 1.6, 0, TAU); c.fill(); }
        });
      }
    }
    _drawParse() {
      const items = (this.event && this.event.segments || []).slice(0, 4), w = this.width, h = this.height, age = (this.clock - this.phaseAt) / 1000;
      items.forEach((s, i) => {
        const a = i * 1.8 + age * .14, x = w * (this.mobile ? .50 : .39) + Math.cos(a) * w * (this.mobile ? .15 : .055), y = h * (this.mobile ? .28 : .432) + Math.sin(a) * h * (this.mobile ? .07 : .11);
        const rgb = s.type === 'fact' ? '116,240,195' : s.type === 'noise' ? '147,171,189' : s.type === 'intent' ? '116,222,247' : '184,164,255';
        this._pill(short(s.text, this.mobile ? 8 : 12), x, y, rgb, Math.min(1, age * 4 + .1), w * (this.mobile ? .30 : .2), 12);
      });
    }
  }
  window.NeuralFlow = NeuralFlow;
})();
