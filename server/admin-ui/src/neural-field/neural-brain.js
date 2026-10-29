(function () {
  'use strict';

  const PALETTE = {
    idle: [79, 201, 224], input: [97, 230, 245], parse: [170, 160, 255],
    archive: [106, 175, 244], memory: [121, 242, 199], affect: [251, 180, 125],
    world: [177, 156, 255], recall: [116, 237, 206], respond: [130, 226, 246]
  };
  const CENTERS = {
    input: [-.82, .12, -.55], parse: [.1, .52, -.32], archive: [.5, -.4, .7],
    memory: [-.45, .48, .36], affect: [.52, -.22, -.62], world: [.55, .42, .64],
    recall: [-.47, .52, .28], respond: [.05, .2, -1.05], idle: [0, 0, 0]
  };
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  function randomFactory(seed) {
    return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }

  class NeuralBrain {
    constructor(canvas) {
      if (!canvas || typeof canvas.getContext !== 'function') throw new TypeError('NeuralBrain requires a canvas element');
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d', { alpha: true });
      this.phase = 'idle'; this.paused = false; this.dead = false;
      this.clock = 0; this.last = 0; this.raf = 0; this.pulses = [];
      this.pointer = { x: 0, y: 0 }; this.rotation = { x: 0, y: 0 };
      this.width = 1; this.height = 1; this.frame = 0;
      this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!canvas.hasAttribute('role')) canvas.setAttribute('role', 'img');
      if (!canvas.hasAttribute('aria-label')) canvas.setAttribute('aria-label', '点状脑图：输入、记忆、情绪与世界线的模拟信息流');
      this._makeGeometry(); this._makeSprites();
      this._onPointer = e => { const r = canvas.getBoundingClientRect(); this.pointer.x = clamp((e.clientX - r.left) / Math.max(r.width, 1) - .5, -.5, .5); this.pointer.y = clamp((e.clientY - r.top) / Math.max(r.height, 1) - .5, -.5, .5); if (this.reduced) this._render(); };
      this._onLeave = () => { this.pointer.x = 0; this.pointer.y = 0; };
      canvas.addEventListener('pointermove', this._onPointer, { passive: true });
      canvas.addEventListener('pointerleave', this._onLeave, { passive: true });
      this._observer = new ResizeObserver(() => this._resize());
      this._observer.observe(canvas);
      this._resize(); this._tick = this._tick.bind(this);
      if (!this.reduced) this.raf = requestAnimationFrame(this._tick);
    }

    _makeGeometry() {
      const rng = randomFactory(90218); this.nodes = [];
      const add = (x, y, z, type, hemi, seed, crease = 0) => this.nodes.push({ x, y, z, type, hemi, seed, crease, sx: 0, sy: 0, depth: 0, radius: .65 + rng() * .68, heat: 0 });
      // Separate, folded cerebral hemispheres. The medial gap is preserved rather
      // than creating one ellipsoid and merely tinting its two halves.
      const n = 960, golden = Math.PI * (3 - Math.sqrt(5));
      for (const hemi of [-1, 1]) {
        for (let i = 0; i < n; i++) {
          const nx = (i + .5) / n;
          const rr = Math.sqrt(1 - nx * nx);
          const angle = i * golden + (hemi === 1 ? .28 : 0);
          let ny = Math.sin(angle) * rr, nz = Math.cos(angle) * rr;
          const foldA = Math.pow(Math.abs(Math.sin(nz * 10.2 + ny * 2.6 + 1.35 * Math.sin(ny * 5.2))), 7);
          const foldB = Math.pow(Math.abs(Math.sin(ny * 11.4 - nz * 3.1 + .9 * Math.cos(nx * 8))), 10);
          const mainSulcus = Math.exp(-Math.pow((nz + .12 + ny * .22) / .075, 2)) * nx;
          const lateralSulcus = Math.exp(-Math.pow((ny + .2 + nz * .16) / .06, 2)) * nx;
          const crease = clamp(.65 * foldA + .35 * foldB + .45 * mainSulcus + .3 * lateralSulcus, 0, 1);
          const fold = 1 - .115 * foldA - .065 * foldB - .08 * mainSulcus - .06 * lateralSulcus;
          const frontal = 1 + .085 * Math.max(0, -nz);
          const temporal = Math.exp(-Math.pow((ny + .43) / .3, 2)) * .075;
          const medial = .072 + .01 * Math.sin(nz * 7.2 + ny * 3.1);
          let x = hemi * (medial + nx * (.94 + temporal) * fold * frontal);
          let y = ny * (ny < 0 ? .73 : .91) * fold + .12 + .035 * Math.sin(nz * 2.3);
          let z = nz * 1.24 * fold * (1 - .035 * Math.max(0, nz));
          // A slight rearward taper and indented lower frontal surface make the
          // silhouette cerebral rather than spherical.
          y += .035 * Math.max(0, -nz) - .035 * Math.max(0, nz);
          y -= .14 * Math.max(0, 1 - nx / .18) * Math.max(0, ny);
          z += .035 * nx * Math.sin(ny * 4.3);
          add(x, y, z, 'cortex', hemi, rng(), crease);
        }
      }
      // Cerebellum: small, grooved lower rear volume, paired lobes.
      for (let i = 0; i < 240; i++) {
        const yy = 1 - 2 * ((i + .5) / 240), rr = Math.sqrt(1 - yy * yy), a = golden * i;
        const ripples = 1 - .045 * Math.cos(yy * 47);
        add(Math.cos(a) * rr * .59 * ripples, -.65 + yy * .32, .69 + Math.sin(a) * rr * .47, 'cerebellum', 0, rng());
      }
      // A modest brainstem, not a decorative pedestal.
      for (let i = 0; i < 96; i++) {
        const t = i / 95, a = i * golden;
        const r = .105 * (1 - .42 * t);
        add(Math.cos(a) * r, -.77 - t * .49, .35 + t * .15 + Math.sin(a) * r, 'stem', 0, rng());
      }
      this.drawOrder = this.nodes.slice(); this.edges = [];
      const cell = .19, grid = new Map();
      const key = (x, y, z) => x + ',' + y + ',' + z;
      this.nodes.forEach((p, i) => { const k = key(Math.floor(p.x / cell), Math.floor(p.y / cell), Math.floor(p.z / cell)); if (!grid.has(k)) grid.set(k, []); grid.get(k).push(i); });
      for (let i = 0; i < this.nodes.length; i++) {
        if (rng() > .68) continue;
        const p = this.nodes[i], gx = Math.floor(p.x / cell), gy = Math.floor(p.y / cell), gz = Math.floor(p.z / cell), nearest = [];
        for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) for (let c = -1; c <= 1; c++) {
          const arr = grid.get(key(gx + a, gy + b, gz + c)); if (!arr) continue;
          for (const j of arr) {
            if (j <= i) continue; const q = this.nodes[j];
            if (p.hemi !== q.hemi || p.type !== q.type) continue;
            const d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2;
            if (d < .027 && d > .001) nearest.push({ j, d });
          }
        }
        nearest.sort((a, b) => a.d - b.d);
        for (const q of nearest.slice(0, 1)) this.edges.push([i, q.j, rng()]);
      }
      this.edgeCount = this.edges.length;
    }

    _makeSprites() {
      this.sprites = {};
      Object.keys(PALETTE).forEach(k => {
        const s = document.createElement('canvas'); s.width = s.height = 48;
        const c = s.getContext('2d'), rgb = PALETTE[k].join(','), g = c.createRadialGradient(24, 24, 0, 24, 24, 24);
        g.addColorStop(0, 'rgba(' + rgb + ',.8)'); g.addColorStop(.15, 'rgba(' + rgb + ',.35)'); g.addColorStop(1, 'rgba(' + rgb + ',0)');
        c.fillStyle = g; c.fillRect(0, 0, 48, 48); this.sprites[k] = s;
      });
    }

    _resize() {
      if (this.dead) return;
      const r = this.canvas.getBoundingClientRect();
      this.width = Math.max(1, r.width); this.height = Math.max(1, r.height);
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.round(this.width * this.dpr); this.canvas.height = Math.round(this.height * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this._render();
    }

    setPhase(phase) {
      const next = Object.prototype.hasOwnProperty.call(PALETTE, phase) ? phase : 'idle';
      this.phase = next; if (next !== 'idle') this.pulse(next, .85); else this._render();
    }
    pulse(kind, intensity) {
      const k = Object.prototype.hasOwnProperty.call(PALETTE, kind) ? kind : this.phase;
      this.pulses.push({ kind: k, center: CENTERS[k], at: this.clock, intensity: clamp(Number(intensity === undefined ? 1 : intensity) || 0, 0, 2) });
      if (this.pulses.length > 8) this.pulses.shift();
      this._render();
    }
    setPaused(value) {
      this.paused = !!value;
      if (this.paused) { cancelAnimationFrame(this.raf); this.raf = 0; }
      else if (!this.reduced && !this.dead && !this.raf) { this.last = 0; this.raf = requestAnimationFrame(this._tick); }
    }
    destroy() {
      this.dead = true; cancelAnimationFrame(this.raf); this.raf = 0;
      this._observer.disconnect(); this.canvas.removeEventListener('pointermove', this._onPointer); this.canvas.removeEventListener('pointerleave', this._onLeave);
    }

    _tick(now) {
      this.raf = 0; if (this.dead || this.paused) return;
      if (this.last) this.clock += Math.min(60, now - this.last);
      this.last = now; this._render();
      this.raf = requestAnimationFrame(this._tick);
    }

    _project(x, y, z) {
      const cy = this.cosYaw, sy = this.sinYaw, cp = this.cosPitch, sp = this.sinPitch;
      const xx = x * cy + z * sy, zz = z * cy - x * sy;
      const yy = y * cp - zz * sp, depth = y * sp + zz * cp;
      const perspective = 4.8 / (4.8 + depth);
      return { x: this.width * .51 + (xx - yy * .035) * this.scale * perspective, y: this.height * .455 - yy * this.scale * perspective, depth, perspective };
    }

    _render() {
      if (this.dead || !this.ctx || this.width < 2 || this.height < 2) return;
      const c = this.ctx, w = this.width, h = this.height, t = this.clock * .001;
      c.clearRect(0, 0, w, h);
      this.rotation.x += (this.pointer.x - this.rotation.x) * .045;
      this.rotation.y += (this.pointer.y - this.rotation.y) * .045;
      const yaw = -.43 + (this.reduced ? 0 : Math.sin(t * .17) * .065) + this.rotation.x * .22;
      const pitch = -.23 + this.rotation.y * .12;
      this.cosYaw = Math.cos(yaw); this.sinYaw = Math.sin(yaw); this.cosPitch = Math.cos(pitch); this.sinPitch = Math.sin(pitch);
      this.scale = Math.min(w / 3.65, h / 3.15) * 1.1;
      const center = CENTERS[this.phase], color = PALETTE[this.phase], colorText = color.join(',');
      this.pulses = this.pulses.filter(p => this.clock - p.at < 3000);

      // Dim optical field and small reference arcs remain underneath the cortex.
      const glow = c.createRadialGradient(w * .51, h * .45, 0, w * .51, h * .45, Math.min(w, h) * .55);
      glow.addColorStop(0, 'rgba(51,140,187,.065)'); glow.addColorStop(1, 'rgba(51,140,187,0)');
      c.fillStyle = glow; c.fillRect(0, 0, w, h);
      c.lineWidth = .6; c.strokeStyle = 'rgba(105,176,205,.12)';
      c.beginPath(); c.ellipse(w * .51, h * .85, this.scale * 1.4, this.scale * .18, 0, .15, Math.PI * 1.86); c.stroke();
      c.strokeStyle = 'rgba(105,176,205,.065)';
      c.beginPath(); c.ellipse(w * .51, h * .85, this.scale * 1.66, this.scale * .23, 0, Math.PI * .22, Math.PI * 1.62); c.stroke();
      c.save(); c.setLineDash([2, 6]); c.strokeStyle = 'rgba(110,176,205,.07)';
      c.beginPath(); c.moveTo(w * .06, h * .46); c.lineTo(w * .94, h * .46); c.stroke(); c.restore();

      for (const p of this.nodes) {
        const projected = this._project(p.x, p.y, p.z); p.sx = projected.x; p.sy = projected.y; p.depth = projected.depth; p.perspective = projected.perspective;
        const distance = (p.x - center[0]) ** 2 + (p.y - center[1]) ** 2 + (p.z - center[2]) ** 2;
        let heat = this.phase === 'idle' ? .035 : Math.exp(-distance * 2.2) * (.45 + .1 * Math.sin(t * 3));
        for (const pulse of this.pulses) {
          const age = (this.clock - pulse.at) / 1000;
          const d = Math.sqrt((p.x - pulse.center[0]) ** 2 + (p.y - pulse.center[1]) ** 2 + (p.z - pulse.center[2]) ** 2);
          heat += Math.exp(-((d - age * 1.15) ** 2) * 38) * Math.max(0, 1 - age / 3) * pulse.intensity;
        }
        p.heat = clamp(heat, 0, 1.4);
      }
      for (const edge of this.edges) {
        const a = this.nodes[edge[0]], b = this.nodes[edge[1]], heat = (a.heat + b.heat) * .5;
        const depth = clamp((1.65 - (a.depth + b.depth) * .5) / 3.1, 0, 1);
        c.strokeStyle = 'rgba(' + colorText + ',' + (.025 + .075 * depth + .15 * heat).toFixed(3) + ')';
        c.lineWidth = heat > .65 ? .72 : .45; c.beginPath(); c.moveTo(a.sx, a.sy); c.lineTo(b.sx, b.sy); c.stroke();
        if (edge[2] > .976 && !this.reduced) {
          const f = (t * .42 + edge[2] * 47) % 1;
          c.fillStyle = 'rgba(186,244,250,' + (.35 + .35 * heat).toFixed(2) + ')';
          c.beginPath(); c.arc(a.sx + (b.sx - a.sx) * f, a.sy + (b.sy - a.sy) * f, 1, 0, Math.PI * 2); c.fill();
        }
      }
      if (this.frame++ % 5 === 0) this.drawOrder.sort((a, b) => b.depth - a.depth);
      for (const p of this.drawOrder) {
        const front = clamp((1.6 - p.depth) / 3, .12, 1), glint = .9 + .1 * Math.sin(t * .9 + p.seed * 33);
        const relief = 1 - p.crease * .47;
        const alpha = clamp((.25 + front * .65 + p.heat * .3) * glint * relief, .12, 1);
        const base = p.type === 'cortex' ? [93 + front * 52, 181 + front * 57, 219 + front * 35] : [109, 194, 220];
        const mix = clamp(p.heat * .9, 0, 1);
        const red = Math.round(base[0] + (color[0] - base[0]) * mix), green = Math.round(base[1] + (color[1] - base[1]) * mix), blue = Math.round(base[2] + (color[2] - base[2]) * mix);
        const radius = p.radius * p.perspective * (.72 + front * .28) + p.heat * .3;
        if (p.heat > .4 && p.seed > .93) { const size = 12 + p.heat * 9; c.globalAlpha = Math.min(.65, p.heat * .55); c.drawImage(this.sprites[this.phase], p.sx - size * .5, p.sy - size * .5, size, size); c.globalAlpha = 1; }
        c.fillStyle = 'rgba(' + red + ',' + green + ',' + blue + ',' + alpha.toFixed(3) + ')'; c.beginPath(); c.arc(p.sx, p.sy, radius, 0, Math.PI * 2); c.fill();
      }
      this._drawFlow(t);
    }

    _drawFlow(t) {
      if (this.phase === 'idle') return;
      const c = this.ctx, w = this.width, h = this.height, phase = this.phase, rgb = PALETTE[phase].join(',');
      const incoming = phase === 'input' || phase === 'parse' || phase === 'recall';
      const target = this._project.apply(this, CENTERS[phase]);
      const start = incoming ? { x: w * .035, y: h * .49 } : { x: target.x, y: target.y };
      const end = incoming ? { x: target.x, y: target.y } : { x: w * .96, y: phase === 'archive' ? h * .74 : phase === 'memory' ? h * .6 : h * .25 };
      const c1 = { x: start.x + (end.x - start.x) * .35, y: start.y - h * .1 }, c2 = { x: start.x + (end.x - start.x) * .66, y: end.y + h * .06 };
      c.strokeStyle = 'rgba(' + rgb + ',.12)'; c.lineWidth = .7;
      c.beginPath(); c.moveTo(start.x, start.y); c.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, end.x, end.y); c.stroke();
      const point = u => { const v = 1 - u; return { x: v * v * v * start.x + 3 * v * v * u * c1.x + 3 * v * u * u * c2.x + u * u * u * end.x, y: v * v * v * start.y + 3 * v * v * u * c1.y + 3 * v * u * u * c2.y + u * u * u * end.y }; };
      const count = this.reduced ? 5 : 13;
      for (let i = 0; i < count; i++) {
        const u = this.reduced ? (i + .5) / count : (t * .37 + i / count) % 1, p = point(u), alpha = Math.sin(u * Math.PI);
        c.fillStyle = 'rgba(' + rgb + ',' + (.7 * alpha).toFixed(3) + ')'; c.beginPath(); c.arc(p.x, p.y, i % 4 === 0 ? 2 : 1.2, 0, Math.PI * 2); c.fill();
        if (i % 4 === 0) { c.globalAlpha = alpha * .55; c.drawImage(this.sprites[phase], p.x - 10, p.y - 10, 20, 20); c.globalAlpha = 1; }
      }
    }
  }
  window.NeuralBrain = NeuralBrain;
})();
