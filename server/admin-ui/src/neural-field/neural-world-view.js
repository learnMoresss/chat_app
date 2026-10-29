(function () {
  'use strict';
  const TAU = Math.PI * 2;
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
  const sources = [
    { key: 'time', name: '时间节律', color: [116, 179, 229] },
    { key: 'weather', name: '天气环境', color: [118, 217, 223] },
    { key: 'plan', name: '日程待办', color: [176, 163, 235] },
    { key: 'disturbance', name: '偶发扰动', color: [226, 184, 133] }
  ];
  const centralColor = [135, 222, 220];
  const shorten = (value, max) => {
    const chars = Array.from(String(value || ''));
    return chars.length > max ? chars.slice(0, max - 1).join('') + '…' : chars.join('');
  };
  function geometry(count, seed) {
    let state = seed;
    const random = () => { state = (Math.imul(state, 1664525) + 1013904223) | 0; return (state >>> 0) / 4294967296; };
    const points = [];
    for (let i = 0; i < count; i++) {
      const y = 1 - 2 * (i + .5) / count, a = i * 2.39996323, r = Math.sqrt(1 - y * y), shell = .72 + random() * .28;
      points.push({ x: Math.cos(a) * r * shell, y: y * shell * .84, z: Math.sin(a) * r * shell, seed: random(), radius: .6 + random() * .65 });
    }
    return points;
  }
  class WorldlineView {
    constructor(canvas) {
      if (!canvas || !canvas.getContext) throw new TypeError('WorldlineView requires a canvas');
      this.canvas = canvas; this.ctx = canvas.getContext('2d', { alpha: true });
      this.snapshot = null; this.clock = 0; this.last = 0; this.frame = 0;
      this.paused = false; this.dead = false; this.seen = new Set(); this.queue = []; this.flight = null;
      this.sourcePulses = [-10000, -10000, -10000, -10000]; this.mergeAt = -10000;
      this.width = 1; this.height = 1; this.mobile = false;
      this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.sourceGeometry = sources.map((_, index) => geometry(70, 71 + index * 31));
      this.centralGeometry = geometry(240, 1999);
      this.canvas.setAttribute('role', 'img');
      this.canvas.setAttribute('aria-label', '时间、天气、日程和偶发事件共同汇成唯一的当前世界线，再进入角色感知。');
      this._tick = this._tick.bind(this);
      this.observer = new ResizeObserver(() => this._resize()); this.observer.observe(canvas);
      this._resize();
      if (!this.reduced) this.frame = requestAnimationFrame(this._tick);
    }
    update(snapshot, event) {
      if (this.dead) return;
      this.snapshot = snapshot || this.snapshot;
      if (event && event.id && !this.seen.has(event.id)) {
        this.seen.add(event.id);
        if (this.seen.size > 160) this.seen.delete(this.seen.values().next().value);
        const index = event.type === 'weather' ? 1 : event.type === 'disturbance' ? 3 : event.type === 'activity' || event.type === 'conversation' ? 2 : 0;
        const item = { id: event.id, index, text: event.cause || event.summary || '当前处境更新' };
        if (this.reduced) { this.sourcePulses[index] = this.clock; this.mergeAt = this.clock; }
        else {
          this.queue.push(item);
          if (this.queue.length > 4) this.queue.splice(0, this.queue.length - 4);
          if (!this.flight) this._startNext();
        }
      }
      if (snapshot) {
        this.canvas.setAttribute('aria-label', '当前 ' + (snapshot.clock || '') + '，天气 ' + (snapshot.weather && snapshot.weather.label || '') + '，正在' + (snapshot.activity && snapshot.activity.title || '') + '。时间、天气、日程和偶发事件合成同一个世界状态。');
      }
      this._render();
    }
    setPaused(value) {
      this.paused = !!value;
      if (this.paused) { cancelAnimationFrame(this.frame); this.frame = 0; }
      else if (!this.dead && !this.reduced && !this.frame) { this.last = 0; this.frame = requestAnimationFrame(this._tick); }
    }
    destroy() {
      this.dead = true; cancelAnimationFrame(this.frame); this.frame = 0; this.observer.disconnect(); this.queue.length = 0;
    }
    _startNext() {
      if (!this.queue.length) return;
      this.flight = { ...this.queue.shift(), at: this.clock, merged: false };
      this.sourcePulses[this.flight.index] = this.clock;
    }
    _resize() {
      if (this.dead) return;
      const box = this.canvas.getBoundingClientRect();
      this.width = Math.max(1, box.width); this.height = Math.max(1, box.height); this.mobile = this.width < 620;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.round(this.width * dpr); this.canvas.height = Math.round(this.height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); this._render();
    }
    _tick(now) {
      this.frame = 0;
      if (this.dead || this.paused) return;
      if (this.last) this.clock += Math.min(65, now - this.last);
      this.last = now;
      if (this.flight) {
        const age = this.clock - this.flight.at;
        if (age >= 1650 && !this.flight.merged) { this.flight.merged = true; this.mergeAt = this.clock; }
        if (age >= 2550) { this.flight = null; this._startNext(); }
      }
      this._render(); this.frame = requestAnimationFrame(this._tick);
    }
    _sourcePosition(index) {
      return this.mobile ? { x: this.width * [.13, .38, .63, .87][index], y: this.height * .14 } : { x: this.width * (index % 2 ? .32 : .10), y: this.height * (index < 2 ? .26 : .72) };
    }
    _centerPosition() { return { x: this.width * (this.mobile ? .50 : .72), y: this.height * (this.mobile ? .60 : .49) }; }
    _path(index) {
      const a = this._sourcePosition(index), d = this._centerPosition();
      if (this.mobile) return [a, { x: a.x, y: this.height * .39 }, { x: d.x, y: this.height * .40 }, d];
      return [a, { x: this.width * .47, y: a.y }, { x: this.width * .56, y: d.y }, d];
    }
    _point(path, t) {
      const u = 1 - t, [a, b, c, d] = path;
      return { x: u ** 3 * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t ** 3 * d.x, y: u ** 3 * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t ** 3 * d.y };
    }
    _fit(text, width) {
      let label = String(text || '—');
      while (this.ctx.measureText(label).width > width && label.length > 1) label = label.slice(0, -2) + '…';
      return label;
    }
    _text(text, x, y, size, color, width, weight) {
      const c = this.ctx;
      c.save(); c.font = (weight || 400) + ' ' + size + 'px "Microsoft YaHei", system-ui, sans-serif';
      c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = color;
      c.shadowColor = '#091421'; c.shadowBlur = 5;
      c.fillText(this._fit(text, width), x, y); c.restore();
    }
    _cloud(geometry, center, radius, color, energy, spin) {
      const c = this.ctx, rgb = color.join(','), t = this.reduced ? .35 : this.clock * .00009 + spin;
      const cs = Math.cos(t), sn = Math.sin(t), glow = c.createRadialGradient(center.x, center.y, 0, center.x, center.y, radius * 1.4);
      glow.addColorStop(0, 'rgba(' + rgb + ',' + (.04 + energy * .1).toFixed(3) + ')'); glow.addColorStop(1, 'rgba(' + rgb + ',0)');
      c.fillStyle = glow; c.fillRect(center.x - radius * 1.4, center.y - radius * 1.4, radius * 2.8, radius * 2.8);
      for (const p of geometry) {
        const xx = p.x * cs + p.z * sn, zz = p.z * cs - p.x * sn, perspective = 3.4 / (3.4 + zz);
        const breath = this.reduced ? 1 : 1 + .024 * Math.sin(this.clock / 1900 + spin);
        const x = center.x + xx * radius * perspective * breath, y = center.y - p.y * radius * perspective * breath;
        const alpha = clamp(.34 + (1 - zz) * .27 + energy * .23, .10, 1);
        c.fillStyle = 'rgba(' + rgb + ',' + alpha.toFixed(3) + ')';
        c.beginPath(); c.arc(x, y, p.radius * (.85 + energy * .35), 0, TAU); c.fill();
      }
      if (energy > .05) {
        c.strokeStyle = 'rgba(' + rgb + ',' + (energy * .24).toFixed(3) + ')'; c.lineWidth = .7;
        c.beginPath(); c.ellipse(center.x, center.y, radius * (1.16 + (1 - energy) * .3), radius * (.72 + (1 - energy) * .2), -.15, 0, TAU); c.stroke();
      }
    }
    _pill(text, x, y, color, alpha) {
      const c = this.ctx, size = this.mobile ? 11 : 12, maxWidth = this.width * (this.mobile ? .56 : .30);
      c.save(); c.globalAlpha = alpha; c.font = '400 ' + size + 'px "Microsoft YaHei", system-ui, sans-serif';
      const label = this._fit(text, maxWidth), width = c.measureText(label).width + 18, height = 27;
      x = clamp(x, width / 2 + 4, this.width - width / 2 - 4);
      c.fillStyle = 'rgba(10,23,36,.97)'; c.strokeStyle = 'rgba(' + color.join(',') + ',.4)'; c.lineWidth = .6;
      c.beginPath(); c.roundRect(x - width / 2, y - height / 2, width, height, 5); c.fill(); c.stroke();
      c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = 'rgb(' + color.join(',') + ')'; c.fillText(label, x, y + .5); c.restore();
    }
    _render() {
      if (this.dead || !this.ctx || this.width < 2 || this.height < 2) return;
      const c = this.ctx, w = this.width, h = this.height, center = this._centerPosition(), snapshot = this.snapshot || {};
      const sourceRadius = this.mobile ? Math.min(17, w * .048) : Math.min(23, h * .075);
      const centerRadius = this.mobile ? Math.min(38, w * .12) : Math.min(48, h * .16);
      c.clearRect(0, 0, w, h);
      const values = [snapshot.clock || '—', snapshot.weather && snapshot.weather.label || '等待天气', snapshot.activity && snapshot.activity.title || '等待日程', snapshot.disturbance && snapshot.disturbance.active ? snapshot.disturbance.title : '暂无扰动'];
      sources.forEach((source, index) => {
        const path = this._path(index), [a, b, d, e] = path;
        const pulse = clamp(1 - (this.clock - this.sourcePulses[index]) / 2200, 0, 1), rgb = source.color.join(',');
        c.strokeStyle = 'rgba(' + rgb + ',' + (.15 + pulse * .18).toFixed(3) + ')'; c.lineWidth = .8;
        c.beginPath(); c.moveTo(a.x, a.y); c.bezierCurveTo(b.x, b.y, d.x, d.y, e.x, e.y); c.stroke();
        if (index === 0 && !this.reduced) {
          const point = this._point(path, (this.clock / 4800) % 1);
          c.fillStyle = 'rgba(' + rgb + ',.65)'; c.beginPath(); c.arc(point.x, point.y, 1.5, 0, TAU); c.fill();
        }
        this._cloud(this.sourceGeometry[index], a, sourceRadius, source.color, pulse, index * .8);
        this._text(source.name, a.x, a.y + sourceRadius + 15, this.mobile ? 11 : 12, '#cbdce8', w * (this.mobile ? .235 : .20), 500);
        this._text(values[index], a.x, a.y + sourceRadius + 34, 11, 'rgb(' + rgb + ')', w * (this.mobile ? .22 : .19));
      });
      const energy = clamp(1 - (this.clock - this.mergeAt) / 2000, 0, 1);
      this._cloud(this.centralGeometry, center, centerRadius, centralColor, energy, 1.8);
      this._text('当前世界线', center.x, center.y + centerRadius + 17, this.mobile ? 12 : 13, '#cfecef', w * (this.mobile ? .60 : .27), 500);
      this._text(snapshot.activity && snapshot.activity.title || '四种输入合成同一处境', center.x, center.y + centerRadius + 39, this.mobile ? 11 : 12, '#a8c9d4', w * (this.mobile ? .64 : .28));
      this._text(snapshot.activity && snapshot.activity.location || '等待状态', center.x, center.y + centerRadius + 59, 11, '#8fa6bd', w * (this.mobile ? .60 : .27));
      c.lineWidth = .8; c.strokeStyle = 'rgba(135,222,220,.30)'; c.beginPath();
      if (this.mobile) {
        const x = w * .90, y = h * .87;
        c.moveTo(center.x + centerRadius + 8, center.y); c.quadraticCurveTo(x, center.y, x, y); c.stroke();
        c.beginPath(); c.moveTo(x - 4, y - 6); c.lineTo(x, y); c.lineTo(x + 4, y - 6); c.stroke();
        this._text('进入感知', w * .86, h * .95, 11, '#96b8c6', w * .25);
      } else {
        const end = w * .965;
        c.moveTo(center.x + centerRadius + 9, center.y); c.lineTo(end, center.y); c.stroke();
        c.beginPath(); c.moveTo(end - 6, center.y - 4); c.lineTo(end, center.y); c.lineTo(end - 6, center.y + 4); c.stroke();
        this._text('进入感知', w * .895, center.y - 15, 11, '#96b8c6', w * .14);
      }
      if (this.flight && !this.reduced) {
        const progress = (this.clock - this.flight.at) / 1650;
        if (progress <= 1.25) {
          const t = clamp(progress, 0, 1), ease = t * t * (3 - 2 * t), p = this._point(this._path(this.flight.index), ease);
          const alpha = Math.min(1, progress * 7) * (progress > 1 ? clamp(1 - (progress - 1) / .25, 0, 1) : 1);
          const offsetY = this.mobile ? 8 : -13;
          this._pill(shorten(this.flight.text, this.mobile ? 17 : 27), p.x, p.y + offsetY, sources[this.flight.index].color, alpha);
        }
      }
    }
  }
  window.WorldlineView = WorldlineView;
})();
