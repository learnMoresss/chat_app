/** Neural Field UI — mount into #neural-field-demo (or given root).
 *  options.mode === 'monitor'：只读监控主路径（不发消息、不权威推进本地世界线）。
 *  options.fetchLive(): Promise<{board, worldline, traces}>
 *  options.personaName?: string
 */
export function mountNeuralField(rootEl, options = {}) {
  const root = rootEl || document.getElementById('neural-field-demo');
  if (!root) return () => {};
  if (root._neuralCleanup) root._neuralCleanup();

  const live = options && options.mode === 'monitor';
  const fetchLive = live && typeof options.fetchLive === 'function' ? options.fetchLive : null;
  const personaLabel = (options && options.personaName) || '';

  const $ = id => root.querySelector('#' + id);
  const engine = new window.CognitiveDemoEngine();
  const brain = new window.NeuralBrain($('nf-brain'));
  const flow = new window.NeuralFlow($('nf-flow'));
  const world = new window.WorldlineSimulation();
  const worldView = new window.WorldlineView($('nf-world-convergence'));
  engine.externalWorld = true;
  const worldScenario = engine.scenarios.find(item => item.id === 'world');
  if (worldScenario) { worldScenario.text = '外面开始下雨了，看看原来的安排要不要调整。'; worldScenario.label = '天气改变计划'; }
  root.querySelector('.nf-workbench').before(root.querySelector('.nf-composer'));
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const emotionKeys = ['joy', 'curiosity', 'excitement', 'worry', 'sadness', 'annoyance'];
  const emotionNames = { joy: '愉悦', curiosity: '好奇', excitement: '兴奋', worry: '担忧', sadness: '低落', annoyance: '不悦' };
  const stages = [
    { at: 0, key: 'input', label: '感知输入', note: '一段新的经历，进入注意区' },
    { at: 2600, key: 'parse', label: '拆解与理解', note: '识别事实、感受与行动线索' },
    { at: 4400, key: 'archive', label: '原文完整归档', note: '全部保留；可以压缩，也可以回查' },
    { at: 6300, key: 'memory', label: '选择性沉淀', note: '只提炼重要内容，其余退出注意区' },
    { at: 8200, key: 'affect', label: '情绪响应', note: '事件带来微小、连续的心情变化' },
    { at: 10100, key: 'world', label: '世界线与意图', note: '延续当前处境，形成下一步意图' },
    { at: 12200, key: 'respond', label: '带着状态回应', note: '记忆、心情与当前经历共同影响表达' }
  ];
  const stageMarks = Array.from(root.querySelectorAll('.nf-phase-track span'));
  const queue = [];
  let mode = 'chat', paused = false, auto = !live, autoIndex = 0;
  let active = null, stageIndex = -1, elapsed = 0, gap = 650, last = 0, frame = 0, dead = false;
  let compressionRevision = 0;
  let worldState = world.snapshot(), lastWorldPaint = -1000, lastWorldRevision = -1;
  let lastEmotionEpoch = worldState.epochMs, lastAffectAt = worldState.epochMs;
  let liveLatestTraceId = '';
  let livePollTimer = 0;
  let liveAdapter = null;
  let lastLiveTrace = null;
  const affectBaseline = { ...engine.state.emotion };
  const archivedWorldIds = new Set();
  let radarValues = { ...engine.state.emotion }, radarStart = { ...radarValues }, radarTarget = { ...radarValues }, radarElapsed = 1000;
  let radarGeometry = null, radarPolygon = null, radarDots = [], radarLabels = [];
  const ns = 'http://www.w3.org/2000/svg';
  const node = (tag, cls, text) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const svgNode = (tag, attrs) => {
    const element = document.createElementNS(ns, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  };
  const points = (radius, cx, cy) => emotionKeys.map((_, i) => {
    const angle = -Math.PI / 2 + i * Math.PI / 3;
    return [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius];
  });
  function buildRadar() {
    const svg = $('nf-radar');
    const width = Math.max(120, svg.getBoundingClientRect().width);
    const height = Math.max(150, svg.getBoundingClientRect().height);
    const cx = width / 2, cy = height / 2 - 3, radius = Math.max(28, Math.min(width / 2 - 42, height / 2 - 36));
    radarGeometry = { cx, cy, radius };
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svg.replaceChildren();
    const title = svgNode('title'); title.textContent = '六维情绪，0 到 100 的模拟状态'; svg.append(title);
    for (const fraction of [.25, .5, .75, 1]) {
      svg.append(svgNode('polygon', { points: points(radius * fraction, cx, cy).map(p => p.join(',')).join(' '), fill: fraction === 1 ? 'rgba(117,233,244,.015)' : 'none', stroke: 'rgba(152,181,209,.15)', 'stroke-width': .7 }));
    }
    points(radius, cx, cy).forEach(p => svg.append(svgNode('line', { x1: cx, y1: cy, x2: p[0], y2: p[1], stroke: 'rgba(152,181,209,.12)', 'stroke-width': .7 })));
    radarPolygon = svgNode('polygon', { fill: 'rgba(117,233,244,.17)', stroke: '#75e9f4', 'stroke-width': 1.4, 'stroke-linejoin': 'round' });
    svg.append(radarPolygon);
    radarDots = emotionKeys.map(() => { const circle = svgNode('circle', { r: 2, fill: '#b6f6f9' }); svg.append(circle); return circle; });
    radarLabels = emotionKeys.map((key, i) => {
      const [x, y] = points(radius + 21, cx, cy)[i];
      const label = svgNode('text', { x, y: y - 3, 'text-anchor': 'middle', class: 'nf-radar-label' });
      label.textContent = emotionNames[key];
      const value = svgNode('text', { x, y: y + 11, 'text-anchor': 'middle', class: 'nf-radar-val' });
      svg.append(label, value); return value;
    });
    paintRadar();
  }
  function paintRadar() {
    if (!radarGeometry || !radarPolygon) return;
    const { cx, cy, radius } = radarGeometry;
    const coords = emotionKeys.map((key, i) => {
      const angle = -Math.PI / 2 + i * Math.PI / 3;
      const value = radarValues[key] || 0;
      const x = cx + Math.cos(angle) * radius * value / 100, y = cy + Math.sin(angle) * radius * value / 100;
      radarDots[i].setAttribute('cx', x.toFixed(2)); radarDots[i].setAttribute('cy', y.toFixed(2));
      radarLabels[i].textContent = String(Math.round(value));
      return x.toFixed(2) + ',' + y.toFixed(2);
    });
    radarPolygon.setAttribute('points', coords.join(' '));
  }
  function animateAffect(result) {
    radarStart = { ...radarValues };
    radarTarget = { ...engine.state.emotion };
    radarElapsed = reduced ? 1000 : 0;
    const target = $('nf-affect-deltas'); target.replaceChildren();
    const label = affectPhrase(result.emotionDelta || {});
    target.append(node('span', 'nf-delta' + (label === '稍有挂念' ? ' neg' : ''), label));
    target.append(node('span', 'nf-delta neutral', '随后逐渐平复'));
    flow.setSemanticState({ affectText: label });
    if (reduced) { radarValues = { ...radarTarget }; paintRadar(); }
    $('nf-radar').setAttribute('aria-label', emotionKeys.map(key => emotionNames[key] + Math.round(radarTarget[key])).join('，'));
  }
  function renderFacts(result) {
    const target = $('nf-facts'); target.replaceChildren();
    const valid = engine.state.facts.filter(f => f.status === 'active');
    const episodes = engine.state.episodes;
    $('nf-fact-count').textContent = String(valid.length + episodes.length);
    const visible = valid.slice().reverse().slice(0, 2);
    if (result && result.mode === 'correction') {
      const revised = engine.state.facts.filter(f => f.status === 'superseded' && result.facts.some(p => p.key === f.key)).slice(-1)[0];
      if (revised) visible.push(revised);
    } else if (episodes.length) {
      const episode = episodes[episodes.length - 1];
      visible.push({ text: episode.title, status: 'active', origin: episode.domain === 'simulated' ? 'world_episode' : 'episode', heat: null });
    }
    visible.forEach(fact => {
      const row = node('div', 'nf-fact' + (fact.status === 'superseded' ? ' superseded' : ''));
      row.append(node('span', 'nf-fact-dot'));
      const body = node('div', 'nf-fact-main', fact.text);
      body.append(node('span', 'nf-fact-state', fact.status === 'superseded' ? '旧版本 · 已退出召回' : fact.origin === 'seed' ? '初始演示设定' : fact.origin === 'world_episode' ? '虚构世界经历' : fact.origin === 'episode' ? '共同经历 · 回链原文' : '当前有效事实'));
      row.append(body);
      if (fact.heat !== null && fact.status !== 'superseded') row.append(node('span', 'nf-fact-heat', fact.heat >= 55 ? '近期相关' : '可检索'));
      target.append(row);
    });
    if (result && result.mode === 'small-talk') $('nf-recall-note').textContent = '原文已留，本轮无需新增长期事实。';
    else if (result && result.mode === 'recall') $('nf-recall-note').textContent = result.recall.length ? '仅召回 ' + result.recall.length + ' 条相关记忆。' : '没有相关记忆，不拿无关热记忆凑数。';
    else if (result && result.mode === 'correction') $('nf-recall-note').textContent = '纠正生效；保留原文与版本来源。';
    else $('nf-recall-note').textContent = '热度影响召回，不改变事实。';
  }
  const formatBytes = value => value < 1024 ? value + ' B' : (value / 1024).toFixed(1) + ' KB';
  const formatTime = epoch => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(epoch));
  function affectPhrase(delta) {
    if ((delta.worry || 0) < 0 && (delta.joy || 0) > 0) return '松了一口气';
    if ((delta.joy || 0) >= 4) return '真心替你高兴';
    if ((delta.worry || 0) > 0) return '稍有挂念';
    if ((delta.joy || 0) > 0) return '轻轻放松下来';
    if ((delta.curiosity || 0) > 0) return '多了一点好奇';
    if ((delta.joy || 0) < 0) return '有一点失落';
    return '保持平稳';
  }
  function renderWorldPlan(state) {
    const target = $('nf-world-plan'); target.replaceChildren();
    const current = Math.max(0, state.plan.findIndex(item => item.status === 'current' || item.status === 'adjusted'));
    const start = Math.min(Math.max(0, current - 1), Math.max(0, state.plan.length - 5));
    state.plan.slice(start, start + 5).forEach(item => {
      const row = node('div', 'nf-plan-item'); row.dataset.status = item.status;
      row.append(node('span', 'nf-plan-time nf-mono', item.time), node('span', 'nf-plan-title', item.title), node('span', 'nf-plan-status', item.status === 'current' ? '当前时段' : item.status === 'adjusted' ? '已调整' : item.status === 'done' ? '已过时段' : '待开始'));
      target.append(row);
    });
  }
  function renderWorldEvents(state) {
    const target = $('nf-world-events'); target.replaceChildren();
    state.events.slice(-4).reverse().forEach(event => {
      const row = node('div', 'nf-world-log'); row.append(node('span', 'nf-world-log-time nf-mono', event.clock));
      const body = node('div', 'nf-world-log-copy');
      body.append(node('strong', '', event.summary || '世界事件'));
      const detail = [event.cause, event.effect].filter(Boolean).join(' · ');
      if (detail) body.append(node('p', '', detail));
      row.append(body); target.append(row);
    });
    const lastEvent = state.events[state.events.length - 1];
    const causeEl = $('nf-world-cause');
    if (causeEl) {
      const line = (lastEvent && (lastEvent.summary || lastEvent.effect)) || state.summary || '';
      causeEl.textContent = line;
      causeEl.hidden = !line;
    }
  }
  function setWorldAlias(state) {
    engine.state.world = { clock: state.clock, day: 1, location: state.activity.location, activity: state.activity.title, concern: state.concern, next: state.intent, revision: state.revision, domain: 'simulated' };
  }
  function decayAffectTo(epochMs) {
    const elapsedMs = Math.max(0, epochMs - lastEmotionEpoch);
    if (elapsedMs > 0) {
      const factor = Math.pow(2, -elapsedMs / (40 * 60000));
      emotionKeys.forEach(key => { engine.state.emotion[key] = affectBaseline[key] + (engine.state.emotion[key] - affectBaseline[key]) * factor; });
    }
    lastEmotionEpoch = epochMs;
  }
  function processWorldEvents(events, state) {
    if (!events.length) return;
    events.forEach(event => {
      if (archivedWorldIds.has(event.id)) return;
      archivedWorldIds.add(event.id);
      decayAffectTo(Math.max(lastEmotionEpoch, event.epochMs));
      engine.state.archive.push({ id: event.id, kind: 'world', text: event.summary + '\n原因：' + event.cause + '\n变化：' + event.effect, reply: event.decision, at: event.clock, day: 1, dateLabel: state.dateLabel, origin: 'world_event' });
      const delta = event.affectDelta || {};
      emotionKeys.forEach(key => { engine.state.emotion[key] = Math.max(0, Math.min(100, engine.state.emotion[key] + (delta[key] || 0))); });
      if (Object.values(delta).some(value => value !== 0)) lastAffectAt = event.epochMs;
      if (event.type === 'activity' && state.activity.status === 'adapted') engine.state.episodes.push({ id: event.id + '-episode', title: event.summary, text: event.cause + '；' + event.effect, domain: 'simulated', sourceEventId: event.id, at: event.clock });
    });
    decayAffectTo(state.epochMs);
    const focus = events.find(event => event.type === 'weather') || events.find(event => event.type === 'disturbance') || events[events.length - 1];
    const combinedDelta = {};
    events.forEach(event => Object.entries(event.affectDelta || {}).forEach(([key, value]) => { combinedDelta[key] = (combinedDelta[key] || 0) + value; }));
    const merged = { ...focus, id: 'merged-' + events.map(event => event.id).join('-'), contextText: state.summary, affectText: affectPhrase(combinedDelta), intentText: state.activity.status === 'adapted' ? '调整成室内活动' : state.activity.status === 'interrupted' ? '先处理眼前的小事' : '继续' + state.activity.title };
    worldView.update(state, merged);
    if (focus.type !== 'conversation') flow.ingestWorld(merged, state);
    brain.pulse('world', .45);
    if (!active) {
      $('nf-appraisal').textContent = focus.cause + '，现在' + state.activity.title + '。';
      $('nf-intention').textContent = state.intent;
      $('nf-stage-tag').textContent = '世界持续推进';
      $('nf-phase-name').textContent = '当前处境更新 · 不需要先有一条聊天';
      $('nf-phase-counter').textContent = 'LIVE';
      $('nf-neural-note').textContent = live ? (state.activity.title || '处境更新') : (state.activity.reason || '');
      $('nf-event-number').textContent = 'WORLD · ' + state.clock;
      stageMarks.forEach(mark => mark.classList.remove('current'));
      animateAffect({ emotionDelta: combinedDelta });
    }
    renderArchive();
    if (!active) renderFacts();
    flow.setSemanticState({ archiveText: '对话与事件可回查' });
  }
  function updateWorld(nowMs, force) {
    if (live) {
      // 监控模式：世界快照只来自后端 hydrate，不推进本地仿真
      if (force || nowMs - lastWorldPaint >= 300) {
        lastWorldPaint = nowMs;
        renderWorld();
        if ((!active || stageIndex >= 4) && radarElapsed >= 1000) {
          radarValues = { ...engine.state.emotion }; paintRadar();
        }
      }
      return;
    }
    const before = worldState;
    worldState = world.tick(nowMs);
    if (worldState.epochMs < before.epochMs || worldState.mode !== before.mode) lastEmotionEpoch = worldState.epochMs;
    setWorldAlias(worldState);
    processWorldEvents(world.drainEvents(), worldState);
    decayAffectTo(worldState.epochMs);
    if (force || nowMs - lastWorldPaint >= 300) {
      lastWorldPaint = nowMs;
      renderWorld();
      if ((!active || stageIndex >= 4) && radarElapsed >= 1000) {
        radarValues = { ...engine.state.emotion }; paintRadar();
      }
      if (!active && worldState.epochMs - lastAffectAt > 6 * 60000) {
        flow.setSemanticState({ affectText: '慢慢回到平稳' });
        $('nf-affect-deltas').replaceChildren(node('span', 'nf-delta neutral', '随时间慢慢平复'));
      }
    }
  }
  function applyWorldInput(text) {
    if (/雷雨|暴雨|雷暴/.test(text)) world.setWeather('storm');
    else if (/雨停|放晴|晴朗|出太阳/.test(text)) world.setWeather('clear');
    else if (/下雨|阵雨|小雨/.test(text)) world.setWeather('rain');
    else if (/多云|阴天/.test(text)) world.setWeather('cloudy');
    else if (/门铃|扰动|插曲|打断/.test(text)) world.perturb();
    worldState = world.snapshot();
    setWorldAlias(worldState);
  }
  async function renderArchive() {
    const records = engine.state.archive;
    $('nf-archive-count').textContent = String(records.length);
    const bars = root.querySelectorAll('#nf-archive-bars span');
    bars.forEach((bar, i) => {
      const record = records[i % Math.max(1, records.length)];
      const on = records.length > 0 && i < Math.min(20, records.length * 3);
      bar.style.height = on ? (10 + ((record.text.length * 3 + i * 7) % 27)) + 'px' : '7px';
      bar.style.opacity = on ? String(.45 + (i % 3) * .2) : '.12';
    });
    renderArchiveResults();
    if (!records.length) { $('nf-raw-bytes').textContent = '0 B'; $('nf-compressed-bytes').textContent = '—'; return; }
    const revision = ++compressionRevision;
    const bytes = new TextEncoder().encode(JSON.stringify(records));
    $('nf-raw-bytes').textContent = formatBytes(bytes.byteLength);
    if (typeof CompressionStream === 'undefined') { $('nf-compressed-bytes').textContent = '此浏览器不支持'; return; }
    $('nf-compressed-bytes').textContent = '压缩中';
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
      const data = await new Response(stream).arrayBuffer();
      if (revision === compressionRevision && !dead) $('nf-compressed-bytes').textContent = formatBytes(data.byteLength);
    } catch (_) {
      if (revision === compressionRevision && !dead) $('nf-compressed-bytes').textContent = '保留原文';
    }
  }
  function renderArchiveResults() {
    const target = $('nf-archive-items'); target.replaceChildren();
    const matches = engine.search($('nf-archive-search').value);
    if (!matches.length) {
      target.append(node('div', 'nf-empty', engine.state.archive.length ? '没有匹配记录。试试「面试」或「来福」。' : '新的输入会在归档阶段出现在这里。')); return;
    }
    matches.slice(0, 12).forEach(record => {
      const article = node('article', 'nf-archive-item');
      article.append(node('div', 'nf-record-meta nf-mono', (record.dateLabel || 'DAY ' + String(record.day).padStart(2, '0')) + ' · ' + record.at + ' / ' + (record.kind === 'world' ? '虚构世界事件' : '对话原文')));
      article.append(node('p', '', record.text));
      article.append(node('p', 'nf-muted', (record.origin === 'world_event' ? '当前决定：' : '回应：') + record.reply));
      target.append(article);
    });
    if (matches.length > 12) target.append(node('div', 'nf-empty', '匹配 ' + matches.length + ' 段，显示最近 12 段。可继续缩小关键词。'));
  }
  function renderWorld() {
    const state = worldState;
    $('nf-world-clock').textContent = state.dateLabel + ' · ' + state.clock;
    $('nf-world-activity').textContent = state.activity.location + ' · ' + state.activity.title;
    const concernEl = $('nf-world-concern');
    if (concernEl) {
      concernEl.textContent = state.concern || '';
      concernEl.hidden = !state.concern;
    }
    $('nf-context-live').textContent = state.clock + ' · ' + state.weather.label;
    $('nf-world-time').textContent = state.clock + ':' + String(Math.floor(state.epochMs / 1000) % 60).padStart(2, '0');
    $('nf-world-date').textContent = state.dateLabel + ' · 北京时间';
    if (!live) {
      $('nf-clock-mode').value = state.mode;
      $('nf-weather-mode').value = state.weather.kind;
      $('nf-world-jump').disabled = state.mode === 'realtime';
      $('nf-world-rate').textContent = paused ? '演示暂停，时钟与动画一起暂停。' : state.mode === 'demo' ? '加速演示：1 秒 = 1 分钟。聊天不会推动时钟，天气为模拟。' : '跟随当前时间，按真实速度推进。天气仍为模拟，不代表当地气象。';
    } else {
      const rate = $('nf-world-rate');
      if (rate) rate.textContent = state.enabled === false ? '世界线已关闭' : '世界线进行中';
    }
    $('nf-activity-title').textContent = state.activity.title;
    $('nf-activity-location').textContent = state.activity.location;
    $('nf-activity-status').textContent =
      state.activity.statusLabel ||
      (state.activity.status === 'adapted'
        ? '因环境调整'
        : state.activity.status === 'interrupted'
          ? '已暂停'
          : '进行中');
    $('nf-activity-progress').style.width = Math.round(state.activity.progress * 100) + '%';
    $('nf-activity-progress').parentElement.setAttribute('aria-valuenow', String(Math.round(state.activity.progress * 100)));
    $('nf-activity-window').textContent = formatTime(state.activity.startedAt) + ' — ' + formatTime(state.activity.endsAt) + ' · 时段进度';
    const reasonEl = $('nf-activity-reason');
    if (reasonEl) {
      reasonEl.textContent = state.activity.reason || '';
      reasonEl.hidden = !state.activity.reason;
    }
    if (lastWorldRevision !== state.revision) {
      lastWorldRevision = state.revision;
      renderWorldPlan(state);
      renderWorldEvents(state);
    }
    worldView.update(state);
    flow.setSemanticState({ worldText: state.activity.title });
    if (!active) flow.setSemanticState({ intentText: state.activity.status === 'adapted' ? '按环境调整计划' : state.activity.status === 'interrupted' ? '先处理眼前小事' : '继续当前安排' });
  }
  function updatePlayButton() {
    const play = $('nf-play');
    if (!play || play.hidden) return;
    const running = !paused;
    play.setAttribute('aria-pressed', String(running));
    $('nf-play-glyph').textContent = running ? 'Ⅱ' : '▷';
    $('nf-play-label').textContent = paused ? '继续演示' : '暂停演示';
  }
  function updateQueueNote() {
    const note = $('nf-queue-note');
    if (!note || live) return;
    note.textContent = paused ? '已暂停 · 点击继续播放' : queue.length ? '已接收 ' + queue.length + ' 段 · 依次处理' : auto ? '先演示一轮对话 · 世界持续推进' : '世界线继续推进 · 可输入对话';
  }
  function setMode(next) {
    mode = next;
    root.querySelectorAll('.nf-mode-button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
    $('nf-message').placeholder = mode === 'world' ? '比如：外面下雨了，调整一下原来的安排。' : '比如：我的狗不叫旺财，叫来福。';
  }
  function enterStage(index) {
    if (!active) return;
    stageIndex = index;
    const stage = stages[index];
    const key = index === 3 && active.mode === 'recall' ? 'recall' : stage.key;
    brain.setPhase(key);
    flow.setPhase(key);
    $('nf-stage-tag').textContent = index === 3 && active.mode === 'recall' ? '相关记忆召回' : stage.label;
    $('nf-phase-name').textContent = (index + 1) + ' · ' + (index === 3 && active.mode === 'recall' ? '按需召回，不全量注入' : stage.label);
    $('nf-phase-counter').textContent = String(index + 1).padStart(2, '0') + ' / 07';
    $('nf-neural-note').textContent = stage.note;
    stageMarks.forEach((mark, i) => { mark.classList.toggle('done', i < index); mark.classList.toggle('current', i === index); });
    if (index === 0) {
      $('nf-current-input').textContent = active.input;
      $('nf-source').textContent = active.kind === 'world' ? '世界事件' : '对话';
      $('nf-event-number').textContent = 'EVENT ' + String(engine.state.revision + 1).padStart(3, '0');
      $('nf-tokens').replaceChildren(node('span', 'nf-token noise', '正在感知…'));
      $('nf-affect-deltas').replaceChildren(node('span', 'nf-delta neutral', '等待本轮事件评估'));
      $('nf-response').textContent = '正在接住这段经历…';
      $('nf-appraisal').textContent = '先留意正在发生的事。';
      $('nf-intention').textContent = '接收输入，暂不预设回应。';
    }
    if (index === 1) {
      $('nf-tokens').replaceChildren(...active.segments.map(segment => node('span', 'nf-token ' + (segment.type === 'fact' ? '' : segment.type), segment.text)));
      $('nf-appraisal').textContent = active.appraisal;
    }
    if (index === 2) {
      updateWorld(Date.now(), true);
      if (!live) {
        if (active.kind === 'world') {
          applyWorldInput(active.input);
          active.response = worldState.activity.status === 'adapted' ? '外面在下雨，散步先换成室内活动。等天气合适、还有时间，再出去。' : '这条环境线索记下了。我现在在' + worldState.activity.location + '，先继续' + worldState.activity.title + '。';
        }
        engine.commit(active);
        const record = engine.state.archive.find(entry => entry.id === active.id);
        if (record) record.dateLabel = worldState.dateLabel;
        if (Object.values(active.emotionDelta || {}).some(value => value !== 0)) lastAffectAt = worldState.epochMs;
        if (active.kind === 'chat') world.noteConversation(active);
      } else {
        // 监控回放：只把本轮写入本地 archive 展示，不改权威 facts（已由 board hydrate）
        engine.state.archive.push({
          id: active.id,
          kind: 'chat',
          text: active.input,
          reply: active.response,
          at: worldState.clock,
          day: 1,
          dateLabel: worldState.dateLabel,
          origin: 'live_trace',
        });
      }
      updateWorld(Date.now(), true);
      renderArchive();
      if (!live) {
        $('nf-neural-note').textContent = '原始输入与回应完整归档 · gzip 实际压缩';
      } else {
        $('nf-neural-note').textContent = '本轮已记录';
      }
    }
    if (index === 3) {
      renderFacts(active);
      root.querySelectorAll('#nf-tokens .noise').forEach(token => token.classList.add('released'));
      if (!live) {
        if (active.mode === 'small-talk') $('nf-neural-note').textContent = '不留下长期事实，但这句话没有被删除';
        if (active.mode === 'recall') $('nf-neural-note').textContent = '从沉淀中取回相关内容；其余保持静默';
      }
    }
    if (index === 4) animateAffect(active);
    if (index === 5) { renderWorld(); $('nf-intention').textContent = active.intention; }
    if (index === 6) {
      $('nf-response').textContent = active.response;
      $('nf-announcement').textContent = '本轮完成。' + active.response;
    }
  }
  function begin(item) {
    if (live && item && item._proposal) {
      active = item._proposal;
    } else {
      active = engine.preview(item.text, item.kind);
      if (item.kind === 'world') {
        active.facts = []; active.worldUpdate = {};
        active.emotionDelta = Object.fromEntries(emotionKeys.map(key => [key, 0]));
        active.episode = { title: '一次模拟环境输入', text: item.text, domain: 'simulated' };
        active.appraisal = '先把环境线索放进同一条世界线，检查它是否真的影响眼下的活动。';
        active.intention = '有冲突才调整安排，时间继续按自己的节奏推进。';
      }
    }
    flow.setEvent(active);
    elapsed = 0; stageIndex = -1;
    root.querySelectorAll('.nf-preset').forEach(button => button.classList.toggle('current', button.dataset.scenario === item.id));
    enterStage(0); updateQueueNote(); updatePlayButton();
  }

  function beginFromTrace(trace, adapter) {
    if (!trace || !adapter) return;
    const proposal = adapter.traceToProposal(trace);
    if (!proposal) return;
    lastLiveTrace = trace;
    begin({ text: proposal.input, kind: 'chat', id: proposal.id, _proposal: proposal });
  }
  function addInput(text, kind, id) {
    if (!text.trim()) { $('nf-message').focus(); return false; }
    if (queue.length >= 6) { $('nf-queue-note').textContent = '已有 6 段等待处理，稍等这一轮完成。'; return false; }
    auto = false; paused = false; brain.setPaused(false); flow.setPaused(false); world.setPaused(false); worldView.setPaused(false);
    queue.push({ text: text.trim(), kind, id });
    if (!active) gap = 0;
    updatePlayButton(); updateQueueNote(); return true;
  }
  function tick(now) {
    if (dead) return;
    const dt = last ? Math.min(65, now - last) : 0; last = now;
    if (!paused && !document.hidden) {
      if (Date.now() - lastWorldPaint >= 300) updateWorld(Date.now(), false);
      if (radarElapsed < 1000) {
        radarElapsed = Math.min(1000, radarElapsed + dt);
        const progress = 1 - Math.pow(1 - radarElapsed / 1000, 3);
        emotionKeys.forEach(key => { radarValues[key] = radarStart[key] + (radarTarget[key] - radarStart[key]) * progress; });
        paintRadar();
      }
      if (active) {
        elapsed += dt;
        while (stageIndex + 1 < stages.length && elapsed >= stages[stageIndex + 1].at) enterStage(stageIndex + 1);
        if (elapsed >= 14500) {
          active = null; gap = 650; brain.setPhase('idle'); flow.setPhase('idle');
          if (auto && autoIndex >= 1) { auto = false; updateQueueNote(); }
          $('nf-stage-tag').textContent = queue.length || auto ? '准备下一段经历' : '世界持续推进';
          updatePlayButton();
        }
      } else {
        gap -= dt;
        if (gap <= 0) {
          if (queue.length) begin(queue.shift());
          else if (auto && autoIndex < 1) begin(engine.scenarios[autoIndex++]);
        }
      }
    }
    frame = requestAnimationFrame(tick);
  }
  $('nf-play').addEventListener('click', () => {
    paused = !paused;
    brain.setPaused(paused); flow.setPaused(paused); world.setPaused(paused || live); worldView.setPaused(paused);
    updateWorld(Date.now(), true); updatePlayButton(); updateQueueNote();
  });
  if (!live) {
    $('nf-clock-mode').addEventListener('change', event => {
      world.setMode(event.target.value);
      lastEmotionEpoch = world.snapshot().epochMs;
      lastWorldRevision = -1;
      updateWorld(Date.now(), true);
    });
    $('nf-world-jump').addEventListener('click', () => { world.advanceMinutes(15); updateWorld(Date.now(), true); });
    $('nf-weather-mode').addEventListener('change', event => { world.setWeather(event.target.value); updateWorld(Date.now(), true); });
    $('nf-world-perturb').addEventListener('click', () => {
      const before = world.snapshot().revision;
      world.perturb(); updateWorld(Date.now(), true);
      if (before === worldState.revision) $('nf-world-cause').textContent = '当前处于扰动处理或冷却时段，让角色先继续眼下的安排。';
    });
    root.querySelectorAll('.nf-mode-button').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
    $('nf-form').addEventListener('submit', event => {
      event.preventDefault();
      if (addInput($('nf-message').value, mode)) $('nf-message').value = '';
    });
    engine.scenarios.forEach(scenario => {
      const button = node('button', 'nf-preset cursor-interaction', scenario.label);
      button.type = 'button'; button.dataset.scenario = scenario.id;
      button.addEventListener('click', () => { setMode(scenario.kind); addInput(scenario.text, scenario.kind, scenario.id); });
      $('nf-presets').append(button);
    });
  }
  function toggleArchive(open) {
    $('nf-archive-drawer').hidden = !open;
    $('nf-archive-open').setAttribute('aria-expanded', String(open));
    if (open) { renderArchiveResults(); $('nf-archive-search').focus({ preventScroll: true }); }
    else $('nf-archive-open').focus({ preventScroll: true });
  }
  $('nf-archive-open').addEventListener('click', () => toggleArchive($('nf-archive-drawer').hidden));
  $('nf-archive-close').addEventListener('click', () => toggleArchive(false));
  $('nf-archive-search').addEventListener('input', renderArchiveResults);
  const radarObserver = new ResizeObserver(buildRadar); radarObserver.observe($('nf-radar'));
  root.querySelectorAll('button').forEach(button => button.classList.add('cursor-interaction'));

  function renderLivePanels(panels) {
    const grid = $('nf-live-grid');
    if (grid) grid.hidden = false;
    const intentList = $('nf-intent-list');
    const affectLog = $('nf-affect-log');
    const traceList = $('nf-trace-list');
    const budgetEl = $('nf-intent-budget');
    if (budgetEl && panels.budget) {
      const b = panels.budget;
      budgetEl.textContent =
        b.messageLeft == null
          ? '预算 —'
          : `私聊 ${b.messagesSent}/${b.messageCap} · 圈 ${b.momentsSent}/${b.momentCap}` +
            (b.unanswered >= 2 ? ' · 未回收紧' : '');
    }
    if (intentList) {
      intentList.replaceChildren();
      if (!(panels.intents || []).length) {
        intentList.append(node('div', 'nf-empty', '暂无排队中的主动私聊/朋友圈'));
      } else {
        panels.intents.forEach((it) => {
          const row = node('div', 'nf-live-item');
          const meta = node('div', 'nf-live-meta');
          meta.append(node('span', 'nf-live-tag ' + (it.source === '对话' ? 'chat' : 'world'), it.source));
          meta.append(document.createTextNode(`${it.kind}${it.ctype ? ' · ' + it.ctype : ''} · ${it.when}`));
          row.append(meta);
          row.append(node('div', '', it.reason));
          intentList.append(row);
        });
      }
    }
    if (affectLog) {
      affectLog.replaceChildren();
      if (!(panels.affectEvents || []).length) {
        affectLog.append(node('div', 'nf-empty', '尚无心情事件'));
      } else {
        panels.affectEvents.forEach((ev) => {
          const row = node('div', 'nf-live-item');
          const meta = node('div', 'nf-live-meta');
          meta.append(node('span', 'nf-live-tag ' + ev.source, ev.sourceLabel));
          meta.append(document.createTextNode(ev.clock + (ev.deltas ? ' · ' + ev.deltas : '')));
          row.append(meta);
          row.append(node('div', '', ev.reason));
          affectLog.append(row);
        });
      }
    }
    if (traceList) {
      traceList.replaceChildren();
      if (!(panels.recentTraces || []).length) {
        traceList.append(node('div', 'nf-empty', '尚无回合记录 · 聊几句后会出现'));
      } else {
        panels.recentTraces.forEach((t) => {
          const row = node('div', 'nf-live-item');
          const meta = node('div', 'nf-live-meta');
          meta.append(document.createTextNode(`${t.clock} · 召回 ${t.mems} 条`));
          row.append(meta);
          row.append(node('div', '', '主人：' + (t.user || '—')));
          if (t.assistant) row.append(node('div', 'nf-muted', '回复：' + t.assistant));
          if (t.presence) row.append(node('div', 'nf-muted', '在场：' + t.presence));
          traceList.append(row);
        });
      }
    }
  }

  async function setupMonitor() {
    world.setPaused(true);
    root.classList.add('nf-mode-monitor');
    const navLive = root.querySelector('.nf-live');
    if (navLive) navLive.innerHTML = '<span class="nf-live-dot"></span>实时监控';
    const ver = root.querySelector('.nf-nav-right .nf-mono');
    if (ver) ver.textContent = personaLabel ? personaLabel.slice(0, 16) : '同步中…';
    const eyebrow = root.querySelector('.nf-eyebrow');
    if (eyebrow) eyebrow.textContent = 'BOARD → WORLDLINE → TURN-TRACE → INTENT';
    const heading = root.querySelector('.nf-heading h2');
    if (heading) heading.innerHTML = '主路径过程<span>监控</span>';
    const sub = root.querySelector('.nf-heading p');
    if (sub) sub.textContent = '数据来自服务器只读轮询，不是本地演示推演。';
    // 监控页去掉演示按钮 / 输入区 / 预设
    const play = $('nf-play');
    if (play) play.hidden = true;
    const composer = root.querySelector('.nf-composer');
    if (composer) composer.hidden = true;
    const controls = root.querySelector('.nf-world-controls');
    if (controls) controls.hidden = true;
    const worldSub = root.querySelector('.nf-world-subtitle');
    if (worldSub) worldSub.textContent = '后端状态 · 只读轮询';
    const worldPanelSub = $('nf-world-panel-sub');
    if (worldPanelSub) worldPanelSub.textContent = '当前处境';
    const thoughtHead = root.querySelector('.nf-thought-panel .nf-panel-head .nf-sub');
    if (thoughtHead) thoughtHead.textContent = '本轮判断';
    const thoughtLabel = root.querySelector('.nf-thought-label');
    if (thoughtLabel) thoughtLabel.textContent = '回合策略';
    const replyLabel = root.querySelector('.nf-reply-label');
    if (replyLabel) replyLabel.textContent = '本轮回复';
    const footer = root.querySelector('.nf-footer');
    if (footer) {
      footer.innerHTML =
        '<span>记忆 / 心情 / 意图 / 世界线均来自 companion API</span><span>只读 · 约 3.5s 刷新</span>';
    }
    // 清掉演示占位文案，避免第一眼像 demo
    $('nf-current-input').textContent = '等待本轮对话…';
    $('nf-response').textContent = '尚无回合。用户发消息后会在这里回放。';
    $('nf-appraisal').textContent = '同步服务器状态中…';
    $('nf-intention').textContent = '—';
    $('nf-stage-tag').textContent = '同步中';
    $('nf-phase-name').textContent = '接入真实状态';
    $('nf-phase-counter').textContent = 'LIVE';
    $('nf-neural-note').textContent = personaLabel ? personaLabel + ' · 监控中' : '监控中';
    $('nf-event-number').textContent = 'LIVE';
    $('nf-tokens').replaceChildren(node('span', 'nf-token noise', '等待真实回合'));
    $('nf-affect-deltas').replaceChildren(node('span', 'nf-delta neutral', '等待心情事件'));
    $('nf-source').textContent = '服务器';

    liveAdapter = await import('./live-adapter.js');
    await pollLive(true);
    livePollTimer = window.setInterval(() => {
      if (!dead && !document.hidden) pollLive(false);
    }, 3500);
  }

  async function pollLive(forceReplay) {
    if (!fetchLive || !liveAdapter || dead) return;
    try {
      const data = await fetchLive();
      const board = data.board || {};
      const worldline = data.worldline || {};
      const traces = data.traces || [];
      const snap = liveAdapter.toWorldSnapshot(board, worldline);
      const panels = liveAdapter.toMonitorPanels(board, worldline, traces);
      worldState = snap;
      lastWorldRevision = -1;
      setWorldAlias(worldState);
      liveAdapter.hydrateEngine(engine, board, snap);
      // 用真实回合/世界事件填原始档案，替换演示 archive
      if (panels.archive && panels.archive.length) {
        engine.state.archive = panels.archive.slice(0, 40);
      }
      radarTarget = { ...engine.state.emotion };
      radarValues = { ...radarTarget };
      paintRadar();
      renderFacts();
      renderArchive();
      renderLivePanels(panels);
      updateWorld(Date.now(), true);
      worldView.update(worldState);
      const meta = liveAdapter.formatMonitorMeta(board, worldline, traces[0]);
      const ver = root.querySelector('.nf-nav-right .nf-mono');
      if (ver) ver.textContent = meta;
      const rate = $('nf-world-rate');
      if (rate) {
        const bits = [];
        if (!snap.enabled) bits.push('世界线已关闭');
        else {
          bits.push('世界线进行中');
          if (snap.weatherSource) bits.push(snap.weatherSource);
          if (snap.interruptPolicy) bits.push(snap.interruptPolicy);
          if (snap.nextAt) {
            try {
              const d = new Date(String(snap.nextAt).replace('Z', '+00:00'));
              bits.push(
                '下次推进 ' +
                  new Intl.DateTimeFormat('zh-CN', {
                    timeZone: 'Asia/Shanghai',
                    hour: '2-digit',
                    minute: '2-digit',
                    hourCycle: 'h23',
                  }).format(d),
              );
            } catch (_) {
              bits.push('下次推进已排程');
            }
          }
        }
        if (panels.lastPlan && panels.lastPlan.trigger) {
          bits.push('规划 ' + panels.lastPlan.trigger + (panels.lastPlan.at ? '@' + panels.lastPlan.at : ''));
        }
        rate.textContent = bits.filter(Boolean).join(' · ');
      }
      // 心情增量：用最新 affect 事件
      if ((panels.affectEvents || []).length && !active) {
        const top = panels.affectEvents[0];
        $('nf-affect-deltas').replaceChildren(
          node('span', 'nf-delta ' + (top.source === 'world' ? 'world' : 'chat'), top.sourceLabel + ' · ' + top.reason + (top.deltas ? ' ' + top.deltas : '')),
        );
      }
      const latest = traces[0] || null;
      if (latest) {
        lastLiveTrace = latest;
        if (latest.id !== liveLatestTraceId) {
          const isFirst = !liveLatestTraceId;
          liveLatestTraceId = latest.id;
          if (!isFirst || forceReplay) {
            if (!active) beginFromTrace(latest, liveAdapter);
          } else if (!active) {
            $('nf-current-input').textContent = latest.user_text || '（等待新回合）';
            $('nf-response').textContent = latest.assistant_text || '—';
            $('nf-appraisal').textContent = latest.affect_strategy || '—';
            $('nf-intention').textContent = latest.world_presence || snap.intent || '—';
            $('nf-stage-tag').textContent = '已接入';
          }
        }
      } else if (!active) {
        $('nf-current-input').textContent = '尚无回合记录 · 与该角色聊几句后会出现';
        $('nf-response').textContent = '—';
        $('nf-appraisal').textContent = snap.summary || '世界状态已同步';
        $('nf-intention').textContent = snap.intent || snap.concern || '—';
        $('nf-stage-tag').textContent = snap.enabled ? '世界线运行中' : '世界线关闭';
      }
    } catch (err) {
      console.error('[neural monitor]', err);
      const tag = $('nf-stage-tag');
      if (tag) tag.textContent = '同步失败';
    }
  }

  buildRadar();
  if (live) {
    setupMonitor().catch(err => console.error('[neural monitor setup]', err));
  } else {
    renderFacts(); updateWorld(Date.now(), true); renderArchive(); updateQueueNote();
  }
  frame = requestAnimationFrame(tick);
  const cleanup = () => {
    dead = true;
    cancelAnimationFrame(frame);
    if (livePollTimer) window.clearInterval(livePollTimer);
    radarObserver.disconnect();
    brain.destroy();
    flow.destroy();
    worldView.destroy();
  };
  root._neuralCleanup = cleanup;
  return cleanup;

}
