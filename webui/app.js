/* AuraFusion web UI — панель управления.
   Страницы описаны декларативно в PAGES: по одной секции на раздел.
   Оформление (tiles, кнопки, списки) — здесь и в style.css. */
'use strict';

let CFG = {}, DEVICES = [], MONITORS = [], AUDIO_NAMES = [];
let lastDevicesTotal = -1, lastSavedAt = 0, satSync = false;

const $ = (s, r) => (r || document).querySelector(s);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt !== undefined) e.textContent = txt;
  return e;
};

/* ================= сеть ================= */
const api = {
  async cfg() { return (await fetch('/api/config')).json(); },
  async styles() { return (await fetch('/api/styles')).json(); },
  async state() { return (await fetch('/api/state')).json(); },
  async audio() { return (await fetch('/api/audio')).json(); },
  async monitors() { return (await fetch('/api/monitors')).json(); },
  async scenes() { return (await fetch('/api/scenes')).json(); },
  async set(patch) {
    await fetch('/api/set', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patch })
    });
  },
  async act(name, args) {
    const r = await fetch('/api/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, args: args || {} })
    });
    return r.json();
  }
};

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 1700);
}

/* ================= плитки ================= */
function tile(title, valueNode) {
  const w = el('div', 'tile');
  if (title) {
    const top = el('div', 'top');
    top.append(el('span', 't', title));
    if (valueNode) top.append(valueNode);
    w.append(top);
  }
  return w;
}

function mkSlider(c) {
  const v = el('span', 'v');
  const w = tile(c.label, v);
  const well = el('div', 'well');       // та же подложка, что у списков
  const s = el('input');
  s.type = 'range';
  s.min = c.min; s.max = c.max; s.step = 1;
  well.append(s);
  w.append(well);
  // Три разные величины, и раньше они были смешаны:
  //
  //   raw    — положение ползунка, целое от c.min до c.max;
  //   stored — что лежит в конфиге: raw / div. Делитель нужен, когда в
  //            конфиге доля, а шкала в процентах: ползунок «Ширина, %» с
  //            div:100 хранит 0.5, а показывает 50;
  //   shown  — что написано рядом с ползунком: stored * scale. Масштаб для
  //            показа ТОЛЬКО: значение в конфиге от него не меняется. Нужен
  //            там, где в конфиге доли, а человеку нужны проценты: порог
  //            тишины 0.004 в конфиге и «0.4%» на экране — одна величина.
  //
  // Раньше mul делал и то и другое сразу: и в конфиг писалось умноженное
  // значение, и показывалось оно же. Из-за этого ползунки области замера
  // показывали 0 вместо 100, и при движении в конфиг попадало 50 вместо 0.5.
  const div = c.div || 1;
  const scale = c.scale || c.mul || 1;
  const paint = (raw) => {
    const stored = raw / div;
    const shown = stored * scale;
    v.textContent = c.int ? String(Math.round(shown)) : shown.toFixed(2);
    s.style.setProperty('--fill', ((raw - c.min) / (c.max - c.min) * 100) + '%');
  };
  const push = () => {
    const out = s.value / div;
    const patch = { [c.key]: c.int ? Math.round(out) : out };
    // Ключ — третьим аргументом: обработчику нужно знать, КАКОЙ ползунок
    // двинулся. Раньше он получал (значение, элемент) и думал, что первое —
    // это ключ: в конфиг уходил мусорный ключ «0.8» вместо color_rw.
    if (c.onchange) c.onchange(out, s, c.key); else put(patch);
  };
  s.addEventListener('input', () => { paint(+s.value); push(); });
  s.addEventListener('change', () => { paint(+s.value); push(); });
  c.setValue = (stored) => {
    s.value = Math.min(c.max, Math.max(c.min, Math.round(stored * div)));
    paint(+s.value);
  };
  return w;
}

function mkCheck(c) {
  const w = el('div', 'tile');
  const l = el('label', 'sw');
  const i = el('input'); i.type = 'checkbox'; i.checked = !!CFG[c.key];
  const track = el('span', 'track');
  track.append(el('span', 'knob'));
  l.append(i, track, el('span', 'lbl', c.label));
  l.addEventListener('click', (e) => {
    // нативный клик по скрытому input не сработает — переключаем сами
    e.preventDefault();
    i.checked = !i.checked;
    if (c.onchange) c.onchange(i.checked, i); else put({ [c.key]: i.checked });
  });
  w.append(l);
  c.el = i;
  return w;
}

/* Свой выпадающий список вместо нативного <select>.
   Нативный попап рисует браузер/ОС: белая рамка, системный синий highlight,
   фон не темы — единого стиля с остальным интерфейсом не получится, CSS
   туда не достанет. Поэтому список целиком наш, как список тем.
   c.el — обёртка с .value, чтобы прежние вызовы c.el.value = X продолжали
   работать без переделки. */
function mkSelect(c) {
  const w = el('div', 'dd');
  if (c.wide) w.style.gridColumn = '1 / -1';
  if (c.label) w.append(el('div', 'fl', c.label));
  const btn = el('button', 'ddbtn');
  const nm = el('span', 'nm');
  const chev = el('span', 'chev', '▼');
  btn.append(nm, chev);
  const menu = el('div', 'ddmenu');
  const list = el('div', 'ddlist');
  menu.append(list);
  const row = el('div', 'ddrow');
  row.append(btn);
  w.append(row, menu);

  const sync = () => {
    [...list.children].forEach((b, i) =>
      b.classList.toggle('on', String((c.options || [])[i]?.[1]) === String(c.el.value)));
  };

  const api_el = {
    _v: undefined,
    get value() { return this._v; },
    set value(v) {
      this._v = v;
      const hit = (c.options || []).find(o => String(o[1]) === String(v));
      nm.textContent = hit ? hit[0] : (v === '' || v === undefined ? '—' : String(v));
      sync();
    },
  };
  c.el = api_el;

  c.setOptions = (opts) => {
    c.options = opts || [];
    list.innerHTML = '';
    for (const [text, value] of c.options) {
      const it = el('button', 'dditem');
      it.append(el('span', null, text));
      it.addEventListener('click', () => {
        api_el.value = value;
        CFG[c.key] = value;
        closeDD();
        // Значение уходит на сервер ВСЕГДА, а обработчик — это уведомление
        // после сохранения, а не его замена. Раньше ветка с onchange
        // пропускала put(), и выбор стиля менялся только в памяти браузера:
        // сервер о нём не узнавал, оверлей рисовал прежнюю фигуру.
        put({ [c.key]: c.isInt ? parseInt(value, 10) : value });
        if (c.onchange) c.onchange(value, api_el);
      });
      list.append(it);
    }
    sync();
  };

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const open = !w.classList.contains('open');
    closeDD();
    if (open) w.classList.add('open');
  });
  menu.addEventListener('click', e => e.stopPropagation());

  c.setOptions(c.options || []);
  api_el.value = CFG[c.key] ?? '';
  if (c.mini) {
    // обновление списка (мониторы / loopback). Класс btn icon — из общей
    // системы кнопок: раньше стоял ghost mini, таких стилей в CSS уже нет,
    // и кнопка выпадала из темы в системную белую
    const b = el('button', 'btn icon', '↻');
    b.title = c.miniTitle || 'Обновить список';
    b.addEventListener('click', async e => { e.stopPropagation(); await c.mini(); toast('список обновлён'); });
    row.append(b);            // в тот же ряд, что и поле — не падает на вторую строку
    w.classList.add('withmini');
  }
  return w;
}

function mkColor(c) {
  const w = tile(c.label);
  const b = el('button', 'chip well');  // подложка как у ползунков и списков
  const lab = el('span');
  const inp = el('input');
  inp.type = 'color';
  inp.style.cssText = 'position:absolute;width:1px;height:1px;opacity:0;border:0;padding:0';
  const hex = () => Array.isArray(CFG[c.key])
    ? '#' + CFG[c.key].map(v => (+v).toString(16).padStart(2, '0')).join('')
    : (CFG[c.key] || '#000000');
  const paint = () => { b.style.background = hex(); lab.textContent = hex().toUpperCase(); };
  const pick = () => { inp.value = hex(); inp.click(); };
  b.append(lab);
  inp.addEventListener('input', () => {
    const v = inp.value.slice(1);
    const rgb = [0, 2, 4].map(i => parseInt(v.substr(i, 2), 16));
    if (Array.isArray(CFG[c.key]) || c.array) {
      CFG[c.key] = rgb; api.set({ [c.key]: rgb });
    } else {
      CFG[c.key] = inp.value; api.set({ [c.key]: inp.value });
    }
    b.style.background = inp.value;
    lab.textContent = inp.value.toUpperCase();
    if (c.onpick) c.onpick();
  });
  b.addEventListener('click', pick);
  w.append(b, inp);
  c.paint = paint;
  paint();
  return w;
}

/* Значение из конфига может устареть (устройство переименовали/отключили) —
   тогда молча падаем на первый пункт, иначе список остаётся без выбора. */
function setSafe(c, want, fallback) {
  const v = String(want ?? '');
  const opts = c.options || [];
  if (v && opts.some(o => String(o[1]) === v)) { c.el.value = v; return; }
  c.el.value = (opts[fallback ?? 0] || [])[1] ?? '';
}

/* ================= синхронизированные контролы ================= */
function onFollow(v) {
  CFG.follow_screen = !!v; api.set({ follow_screen: !!v }); sync('follow');
}
function onSat(v) {
  if (satSync) return;
  satSync = true;
  v = Math.max(0, Math.min(1.5, v));
  CFG.rgb_saturation = v; api.set({ rgb_saturation: v });
  sync('sat'); satSync = false;
}

/* Автозапуск пишется прямо в реестр, а не просто в настройки, поэтому через
   api.set не идёт: сервер всё равно должен знать, включили его или выключили.
   Значение, сказанное панелью, игнорируем — доверяем ответу сервера, он
   видит настоящее состояние записи в Run. Иначе галочка мигнёт «назад»,
   если что-то пошло не так. */
/* Сообщаем серверу, где на экране окно панели: сервер вырежет его из кадра
   замера цвета. Без этого оверлей измеряет сам себя — в панели есть цветные
   кружки цвета экрана и цвета ПК, и при резкой смене картинки на экране
   обратная связь раскачивает цвет оверлея.

   screenX/screenY — положение окна в координатах экрана, outerWidth/Height —
   размер вместе с рамкой. Шлём не чаще раза в 2 с: при переносе окна
   мышью событие move сыпется десятками раз в секунду. */
let panelGeomAt = 0;
async function reportPanelGeom() {
  // скрытая вкладка не отчитывается: у неё window.outerWidth/Height могут
  // быть любыми, в том числе размером экрана. При нескольких открытых
  // вкладках AuraFusion именно скрытая перебивала бы видимую, и сервер
  // считал бы, что окно закрывает весь экран.
  if (document.hidden || document.visibilityState !== 'visible') return;
  const now = Date.now();
  if (now - panelGeomAt < 2000) return;
  panelGeomAt = now;
  try {
    await api.act('panel_geom', {
      x: window.screenX, y: window.screenY,
      w: window.outerWidth, h: window.outerHeight
    });
  } catch (e) { /* панель просто останется незарезанной */ }
}

async function onAutostart(v, box) {
  try {
    const r = await api.act('autostart', { on: !!v });
    const st = (r && r.result) || {};
    const onNow = !!st.enabled;
    CFG.autostart = onNow;
    if (box) box.checked = onNow;
    toast(st.ok ? (onNow ? 'Буду запускаться при входе в Windows'
                           : 'Автозапуск выключен')
                : 'Не вышло: ' + (st.info || 'ошибка'));
  } catch (e) {
    if (box) box.checked = !v;
    toast('Не вышло: ' + e);
  }
}

const REG_PRESETS = {
  full: [0, 0, 1, 1], center: [0.25, 0.25, 0.5, 0.5],
  top: [0, 0, 1, 0.35], bottom: [0, 0.65, 1, 0.35]
};
const REG_KEYS = ['color_rx', 'color_ry', 'color_rw', 'color_rh'];

function onRegion(key) {
  CFG.color_region = key;
  if (key === 'custom') { api.set({ color_region: key }); return; }
  const vals = REG_PRESETS[key] || REG_PRESETS.full;
  const patch = { color_region: key };
  REG_KEYS.forEach((k, i) => { patch[k] = vals[i]; });
  api.set(patch); sync('region');
}
function onRegionCustom(v, el, k) {
  // v — уже значение из конфига (доля 0..1): ползунок сам делит на div.
  // Раньше здесь стояло ещё и деление на 100, и получалось двойное деление:
  // 80 % превращались в 0.008 вместо 0.8, и область замера схлопывалась
  // в ничто. А k — это ключ ползунка, его присылает сам ползунок третьим
  // аргументом: по старинке обработчик брал первым аргументом «значение»,
  // и в конфиг уходил мусорный ключ.
  const patch = { [k]: Math.max(0, Math.min(1, v)) };
  if (CFG.color_region !== 'custom') {
    patch.color_region = 'custom'; CFG.color_region = 'custom';
    sync('regionSelect');
  }
  api.set(patch);
}
function onCmode(v) {
  if (v !== 'custom') { api.set({ color_mode: v }); return; }
  const cur = CFG.custom_color || [0, 220, 255];
  const inp = document.createElement('input');
  inp.type = 'color';
  inp.value = '#' + cur.map(x => (+x).toString(16).padStart(2, '0')).join('');
  inp.addEventListener('input', () => {
    const s = inp.value.slice(1);
    CFG.custom_color = [0, 2, 4].map(i => parseInt(s.substr(i, 2), 16));
    api.set({ custom_color: CFG.custom_color, color_mode: 'custom' });
    sync('custom');
  });
  inp.click();
}
function onAudio(v) { api.set({ audio_device: v }); }
function onStyle(v) {
  // значение уже ушло на сервер обработчиком списка; здесь только
  // подставляем значения этой фигуры и прячем неуместные настройки
  seedStyle(v);
  fillValues('viz');
  fillValues('snd');
  fillValues('sys');
  fillValues('amb');
  applyStyleProfile();
}
function onColorMon(v) { api.set({ color_monitor: parseInt(v, 10) || -1 }); }
function onOvMon(v) { api.set({ monitor: parseInt(v, 10) || 0 }); }
function onSysCheck(k, v) {
  api.set({ [k]: !!v });
  // сервер читает args.on. Раньше здесь было { value: ... }, сервер получал
  // undefined и всегда выключал автозапуск, а панель показывала «включено».
  if (k === 'autostart') onAutostart(!!v);
}

/* ================= настройки по стилям =================
   У каждой фигуры свой набор настроек: кругу не нужна ширина по краям
   экрана, волне — центр на экране. Панель спрашивает у сервера, какие
   ключи уместны для каждой фигуры (ui.overlay.style_keys), прячет
   неуместные, а значения помнит отдельно для каждой фигуры, чтобы
   переключение туда-обратно ничего не затирало. */
let STYLE_KEYS = {};       // стиль -> Set: что показывать
let STYLE_OWN = {};        // стиль -> Set: что писать в личный список
let STYLE_LIST = [];       // [[ключ, подпись], ...]

function scopedKeys() {
  const k = STYLE_KEYS[CFG.style];
  return k ? new Set(k) : null;
}
function ownKeys() {
  const k = STYLE_OWN[CFG.style];
  return k ? new Set(k) : null;
}

/** Кладёт значения в общий конфиг, а настройки, которые у фигуры свои,
    ещё и в её личный список — иначе фигуры затирали бы друг друга.
    Общие настройки (звук, RGB, система) сюда не попадают намеренно. */
function withPerStyle(patch) {
  const own = ownKeys();
  if (!own) return patch;
  let hit = false;
  for (const k of Object.keys(patch)) if (own.has(k)) { hit = true; break; }
  if (!hit) return patch;
  const st = CFG.style || 'smooth';
  const map = Object.assign({}, CFG.style_settings || {});
  map[st] = Object.assign({}, map[st]);
  const rest = {};
  for (const k of Object.keys(patch)) {
    if (own.has(k)) map[st][k] = patch[k];
    else rest[k] = patch[k];   // общие настройки идут в общий конфиг как есть
  }
  CFG.style_settings = map;
  rest.style_settings = map;
  return rest;
}

function put(patch) { api.set(withPerStyle(patch)); }

/** Запоминает текущие значения под новую фигуру при первом переходе на неё,
    иначе фигура начинала бы с чужих чисел и всё бы сдвинулось. */
function seedStyle(st) {
  const own = new Set(STYLE_OWN[st] || []);
  const map = Object.assign({}, CFG.style_settings || {});
  if (map[st]) return;
  const seed = {};
  for (const k of own) if (Object.prototype.hasOwnProperty.call(CFG, k)) seed[k] = CFG[k];
  map[st] = seed;
  CFG.style_settings = map;
  put({ style_settings: map });
}

/** Прячет ползунки и галочки, неуместные для выбранной фигуры,
    и прячет всю секцию, если в ней не осталось ничего живого. */
function applyStyleProfile() {
  const scope = scopedKeys();
  if (!scope) return;
  // Только страница «Визуал». Остальные страницы — это RGB, Ambilight, звук
  // и система: их ключи не входят в профиль фигуры, и раньше проверка
  // прятала их целиком.
  for (const sec of PAGES.viz) {
    let live = 0;
    for (const c of sec.items || []) {
      const node = c._node;
      if (!node) continue;
      // без ключа (кнопки, подсказки, списки устройств) показываем всегда
      const show = !c.key || scope.has(c.key);
      node.style.display = show ? '' : 'none';
      if (show) live++;
    }
    // секция целиком неуместна — прячем и её
    if (sec._grid) sec._grid.parentElement.style.display = live ? '' : 'none';
  }
}

/* ================= страницы ================= */
/* ================= фоновый SVG, живой на музыку =================
   Три дрейфующих пятна + пульсирующие кольца + спектральные штрихи
   по кругу + водяной знак AuraFusion. Всё красится текущими токенами
   темы, поэтому меняет цвет вместе с оформлением. */
const NS = 'http://www.w3.org/2000/svg';
const BG = (() => {
  const CX = 800, CY = 450;
  let svg = null, ticks = [], rings = [], blobEls = [], mark = null;
  let cur = { lvl: 0, bass: 0, spec: [] };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  function mk(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function build() {
    if (svg) return;
    svg = mk('svg', { id: 'bg', viewBox: '0 0 1600 900', preserveAspectRatio: 'xMidYMid slice' });
    svg.setAttribute('aria-hidden', 'true');

    const defs = mk('defs');
    defs.innerHTML = [
      `<radialGradient id="bgA"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".50"/>` +
      `<stop offset="55%" stop-color="var(--accent)" stop-opacity=".10"/>` +
      `<stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></radialGradient>`,
      `<radialGradient id="bgB"><stop offset="0%" stop-color="var(--accent2)" stop-opacity=".46"/>` +
      `<stop offset="55%" stop-color="var(--accent2)" stop-opacity=".10"/>` +
      `<stop offset="100%" stop-color="var(--accent2)" stop-opacity="0"/></radialGradient>`,
      `<radialGradient id="bgC"><stop offset="0%" stop-color="var(--pink)" stop-opacity=".30"/>` +
      `<stop offset="60%" stop-color="var(--pink)" stop-opacity=".06"/>` +
      `<stop offset="100%" stop-color="var(--pink)" stop-opacity="0"/></radialGradient>`,
      `<linearGradient id="bgWord" x1="0" y1="0" x2="1" y2="0">` +
      `<stop offset="0%" stop-color="var(--accent)"/>` +
      `<stop offset="50%" stop-color="var(--accent2)"/>` +
      `<stop offset="100%" stop-color="var(--accent)"/></linearGradient>`,
    ].join('');
    svg.append(defs);

    // --- пятна ---
    const blobs = mk('g', { class: 'bg-blobs' });
    const bspec = [[210, 150, 460, 'bgA'], [1400, 730, 520, 'bgB'], [880, 120, 380, 'bgC']];
    for (const [x, y, r, fill] of bspec) {
      const e = mk('ellipse', { cx: x, cy: y, rx: r, ry: r * 0.78, fill: `url(#${fill})` });
      e.style.animationDelay = (bspec.findIndex(s => s[3] === fill) * -7) + 's';
      blobEls.push(e);
      blobs.append(e);
    }
    svg.append(blobs);

    // --- кольца: радиус и прозрачность от баса ---
    const rg = mk('g', { class: 'bg-rings' });
    for (let i = 0; i < 4; i++) {
      const e = mk('circle', { cx: CX, cy: CY, r: 190 + i * 96, fill: 'none' });
      e.style.setProperty('--i', i);
      rings.push(e);
      rg.append(e);
    }
    svg.append(rg);

    // --- спектр по кругу ---
    const tg = mk('g', { class: 'bg-ticks' });
    const N = 56, R0 = 300;
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2 - Math.PI / 2;
      const e = mk('line', {
        x1: CX + Math.cos(a) * R0, y1: CY + Math.sin(a) * R0,
        x2: CX + Math.cos(a) * R0, y2: CY + Math.sin(a) * R0,
      });
      ticks.push({ e, a, r: R0 });
      tg.append(e);
    }
    svg.append(tg);

    // --- водяной знак ---
    mark = mk('text', { x: CX, y: CY + 58, class: 'bg-mark' });
    mark.textContent = '尺卂几ᗪㄖ爪卂丨乙乙乇尺';
    svg.append(mark);

    document.body.insertBefore(svg, document.body.firstChild);
  }

  function update(s) {
    if (!svg) return;
    cur.lvl = s.level || 0;
    cur.bass = s.bass || 0;
    if (s.spec && s.spec.length) cur.spec = s.spec;

    const lvl = cur.lvl, bass = cur.bass;
    svg.style.setProperty('--lvl', lvl.toFixed(3));
    svg.style.setProperty('--bass', bass.toFixed(3));

    const sp = cur.spec;
    if (sp && sp.length) {
      const RMAX = 118, N = sp.length, M = ticks.length;
      for (let i = 0; i < M; i++) {
        const t = ticks[i];
        // спектр с сервера может быть короче/длиннее штрихов — ресемплим
        const v = sp[Math.min(N - 1, Math.floor(i * N / M))];
        const len = 5 + v * RMAX;
        t.e.setAttribute('x2', (CX + Math.cos(t.a) * (t.r + len)).toFixed(1));
        t.e.setAttribute('y2', (CY + Math.sin(t.a) * (t.r + len)).toFixed(1));
        t.e.style.opacity = (0.18 + v * 0.72).toFixed(3);
        t.e.style.strokeWidth = (1.1 + v * 2.2).toFixed(2);
      }
    }
    if (mark && !reduced) {
      mark.style.opacity = (0.05 + lvl * 0.10).toFixed(3);
    }
  }

  return { build, update };
})();

/* ================= темы оформления =================
   Пять входов на тему: fg (текст), bg, accent, accent2, on (текст на
   акценте). Остальные токены выводит CSS через color-mix — поэтому
   палитра целиком живёт в style.css, а здесь только предпросмотр. */
const THEMES = [
  ['neon', 'Неон', '#00E5FF', '#B388FF', '#0E1116'],
  ['ocean', 'Океан', '#2FD9BE', '#4C8DFF', '#07100F'],
  ['ice', 'Лёд', '#9AD7FF', '#D9EEFF', '#0A1017'],
  ['cobalt', 'Кобальт', '#2F6BFF', '#7BA6FF', '#060A14'],
  ['aurora', 'Аврора', '#3FE0A8', '#8A7BFF', '#070C0E'],
  ['sunset', 'Закат', '#FF9F43', '#FF5C8A', '#140E0B'],
  ['ember', 'Пожар', '#FF5A3C', '#FFB03A', '#100806'],
  ['amber', 'Янтарь', '#FFB020', '#FFE07A', '#120D07'],
  ['terra', 'Терракота', '#E2725B', '#F2A65A', '#120C0A'],
  ['mint', 'Мята', '#7FF0C6', '#4FD1E0', '#08120F'],
  ['forest', 'Лес', '#7BE495', '#D9F26B', '#0A100C'],
  ['moss', 'Мох', '#8FA86B', '#C4D38A', '#0B0F0A'],
  ['lime', 'Лайм', '#A8E10C', '#D9FF3C', '#0B1005'],
  ['coal', 'Уголь', '#E8EDF3', '#9AA4B2', '#0F0F10'],
  ['graphite', 'Графит', '#9FB3C8', '#C7D3DE', '#0D1117'],
  ['ash', 'Пепел', '#C9B8A8', '#9A8B7C', '#100E0D'],
  ['ivory', 'Слоновая кость', '#B45309', '#0F766E', '#F4F1EA'],
  ['amethyst', 'Аметист', '#A78BFA', '#E879F9', '#0D0A14'],
  ['neonrose', 'Неоновый розовый', '#FF3D9A', '#00E5FF', '#100610'],
  ['lavender', 'Лаванда', '#C4B5FD', '#FBCFE8', '#0F0B12'],
];
const THEME_GROUPS = [
  ['Холодные', ['neon', 'ocean', 'ice', 'cobalt', 'aurora']],
  ['Тёплые', ['sunset', 'ember', 'amber', 'terra']],
  ['Зелёные', ['mint', 'forest', 'moss', 'lime']],
  ['Нейтральные', ['coal', 'graphite', 'ash', 'ivory']],
  ['Фиолетово-розовые', ['amethyst', 'neonrose', 'lavender']],
];
const themeById = id => THEMES.find(t => t[0] === id) || THEMES[0];

function applyTheme(id) {
  const t = themeById(id);
  document.documentElement.dataset.theme = t[0];
  try { localStorage.setItem('aura_theme', t[0]); } catch (_) { }
  document.querySelectorAll('.dditem').forEach(b =>
    b.classList.toggle('on', b.dataset.theme === t[0]));
  const nm = $('#ddThemeName');
  if (nm) nm.textContent = t[1];
  const bars = $('#ddThemeBars');
  if (bars) fillBars(bars, t[2], t[3], t[4]);
}

function fillBars(box, a, b, bg) {
  box.innerHTML = '';
  for (const c of [a, b, bg]) {
    const i = document.createElement('i');
    i.style.background = c;
    box.append(i);
  }
}

/* ---------- сцены: наборы настроек одним кликом ---------- */
let SCENES = [];
let activeScene = null;

function mkScenes() {
  const box = el('div', 'scenes');
  box.dataset.scenes = '1';
  renderScenes();
  return box;
}

function renderScenes() {
  const box = document.querySelector('.scenes');
  if (!box) return;
  box.textContent = '';
  if (!SCENES.length) {
    box.append(el('div', 'hint', 'Сцены не загрузились.'));
    return;
  }
  const row = el('div', 'btnrow');
  for (const s of SCENES) {
    const b = el('button', 'btn scene' + (s.id === activeScene ? ' on' : ''), s.name);
    b.title = s.hint || s.name;
    b.addEventListener('click', async () => {
      try {
        const r = await fetch('/api/scene', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: s.id })
        }).then(x => x.json());
        if (!r || r.ok === false) { toast('сцена не применилась'); return; }
        activeScene = s.id;
        renderScenes();
        toast(s.name + ' ✓');
      } catch (e) {
        toast('сервер не ответил');
      }
    });
    row.append(b);
  }
  box.append(row);
}

async function loadScenes() {
  try {
    const r = await api.scenes();
    SCENES = (r && r.scenes) || [];
    renderScenes();
  } catch (e) {
    SCENES = [];
  }
}

function mkThemeDD() {
  // w объявляем сразу: ниже на него ссылается обработчик клика по кнопке.
  // Если перенести объявление в конец функции, вызов упадёт с
  // «Cannot access 'w' before initialization» — и страница «Система»
  // перестанет собираться целиком.
  const w = el('div', 'dd wide');
  const btn = el('button', 'ddbtn');
  const bars = el('span', 'bars'); bars.id = 'ddThemeBars';
  const nm = el('span', 'nm'); nm.id = 'ddThemeName';
  const cnt = el('span', 'cnt', THEMES.length + ' тем');
  const chev = el('span', 'chev', '▼');
  btn.append(bars, nm, cnt, chev);

  const menu = el('div', 'ddmenu');
  const byId = new Map(THEMES.map(t => [t[0], t]));
  for (const [gname, ids] of THEME_GROUPS) {
    menu.append(el('div', 'ddgroup', gname));
    const list = el('div', 'ddlist');
    for (const id of ids) {
      const t = byId.get(id);
      const it = el('button', 'dditem');
      it.dataset.theme = t[0];
      const ib = el('span', 'bars');
      fillBars(ib, t[2], t[3], t[4]);
      it.append(ib, el('span', null, t[1]));
      it.addEventListener('click', () => {
        CFG.web_theme = t[0];
        applyTheme(t[0]);
        api.set({ web_theme: t[0] });
        closeDD();
      });
      list.append(it);
    }
    menu.append(list);
  }

  w.append(btn, menu);
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const open = !w.classList.contains('open');
    document.querySelectorAll('.dd.open').forEach(d => d.classList.remove('open'));
    if (open) w.classList.add('open');
  });
  menu.addEventListener('click', e => e.stopPropagation());
  w.append(btn, menu);
  return w;
}
function closeDD() { document.querySelectorAll('.dd.open').forEach(d => d.classList.remove('open')); }
document.addEventListener('click', closeDD);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDD(); });

/* ================= каталог RGB-устройств ================= */
/* ok    — работает и проверено у нас
   warn  — протокол тот же, но на своём железе не проверялось
   no    — не поддерживается (и почему) */
const RGB_CATALOG = [
  ['OpenRGB (универсальный)', 'материнки, видеокарты, корпусные контроллеры, USB-ленты', 'ok', 'Драйвер/сервер OpenRGB на 127.0.0.1:6742'],
  ['ELK-BLEDOM / ELK-BLEDDM', 'ваша BLE-лента', 'ok', 'Bluetooth · FFF0/FFF3 · 9-байтный кадр'],
  ['ELK-BLEDOB', 'разновидность того же контроллера', 'ok', 'Bluetooth · тот же протокол'],
  ['LED LIGHT STRIP', 'ленты под другими брендами', 'warn', 'Bluetooth · тот же протокол'],
  ['XSL-', 'контроллеры нового поколения', 'warn', 'Bluetooth · тот же протокол'],
  ['ELK~', 'разновидность с волнистым именем', 'warn', 'Bluetooth · тот же протокол'],
  ['ELK-* (шифрованные)', 'модели с шифрованием кадра', 'no', 'нужен слой шифрования из прошивки — не реализован'],
  ['ELK-BLE / LEDBLE / MELK', 'контроллеры других вендоров', 'no', 'протокол отличается, поддержка не добавлена'],
];

function mkCatalog() {
  const box = el('div', 'cat');
  for (const [fam, sub, state, how] of RGB_CATALOG) {
    const row = el('div', 'catrow');
    const f = el('div', 'fam');
    f.append(document.createTextNode(fam));
    f.append(el('small', null, sub));
    const label = { ok: 'работает', warn: 'не проверено', no: 'нет поддержки' }[state];
    row.append(f, el('div', 'how', how), el('span', 'badge ' + state, label));
    box.append(row);
  }
  return box;
}

const PAGES = {
  viz: [
    { g: 'Фигура', desc: 'Что именно рисуем и каким цветом. Остальные секции показывают только настройки выбранной фигуры — лишние прячутся сами.', items: [
      { t: 'select', key: 'style', label: 'Стиль', onchange: onStyle, options: [
        ['Плавная волна', 'smooth'], ['Бары', 'bars'], ['Пики', 'peak'],
        ['Частицы', 'particles'], ['Искры', 'sparks'], ['Шарики', 'orbs'], ['Сетка', 'mesh'],
        ['Слои', 'stack'], ['Заливка', 'fill'], ['Лента', 'ribbon'],
        ['Круг', 'circle'], ['Дуга', 'arc'], ['Спираль', 'spiral'],
        ['Кольца', 'radial'], ['Лучи', 'rays'], ['Бары+волна', 'combo']], desc: 'Фигура, которой рисуется волна. У каждой свой набор настроек: у искр — размер и свечение частиц, у круглых — центр и радиус, у столбиков — высота и ширина полосы.' },
      { t: 'select', key: 'shape_color', label: 'Цвет фигур', desc: 'Бары, круг и прочие фигуры. Пока «Экран → ПК» выключен, берётся палитра.', options: [
        ['Как на экране', 'screen'], ['Палитра, подкрашенная экраном', 'tint'],
        ['Своя палитра', 'palette'] ] },
      { t: 'select', key: 'color_mode', label: 'Палитра', onchange: onCmode, options: [
        ['Неон', 'neon'], ['Зелёный', 'green'], ['Огонь', 'fire'], ['Белый', 'white'], ['Свой...', 'custom']], desc: 'Набор цветов, из которых берётся цвет фигуры. Работает, когда «Цвет фигур» = «Своя палитра». «Свой…» открывает выбор одного цвета.' },
      { t: 'color', key: 'fill_color', label: 'Цвет фигуры (если экран выкл)', array: true, desc: 'Цвет, которым рисуется фигура, когда авто-цвет с экрана выключен. В режиме «Как на экране» этот параметр не действует.' },
    ]},
    { g: 'Размер и положение', desc: 'Где стоит фигура и какого размера. У фигур на базовой линии — своя полоса по низу экрана, у круглых — центр и радиус.', items: [
      { t: 'slider', key: 'line_count', label: 'Количество линий', min: 8, max: 512, int: true, desc: 'Столбики у баров, спицы у круга, витки у спирали. У искр от этого же числа считается бюджет частиц: линии × 4, потолок 2048.' },
      { t: 'slider', key: 'line_smooth', label: 'Плавность линий', min: 0, max: 100, div: 100, desc: '0 — линии ровные и отчётливые, 100 — сливаются в сплошную форму.' },
      { t: 'slider', key: 'shape_glow', label: 'Свечение фигуры', min: 0, max: 300, div: 100, desc: 'Мягкий ореол вокруг всей фигуры, как drop-shadow в CSS. У искр своё свечение частиц, поэтому у них этот ползунок не показывается.' },
      // Показываются только у искр: сервер отдаёт ключи в профиле фигуры
      // (per_style_keys в ui/overlay.py), панель прячет лишнее сама.
      { t: 'slider', key: 'spark_speed', label: 'Скорость искр', min: 5, max: 400, div: 100, desc: 'Базовая скорость подъёма. Пока играет музыка, искры идут по всей площади вот с этой скоростью.' },
      { t: 'slider', key: 'spark_boost', label: 'Разгон на частоте', min: 0, max: 600, div: 100, desc: 'Насколько сильно полоса выстреливает там, где выросла. Медленный фон разгону не подвержен: быстрые частицы появляются на всплеске и обгоняют его. 0 — быстрые летят как фон, 2 — сильный выстрел на пике.' },
      { t: 'slider', key: 'spark_density', label: 'Плотность искр', min: 15, max: 800, div: 100, desc: 'Сколько частиц одновременно в воздухе. Меньше — редкие брызги, больше — плотная завеса. Выше 400 нужно много линий: бюджет частиц считается как «количество линий» × 4, потолок 2048.' },
      { t: 'slider', key: 'spark_size', label: 'Размер искр', min: 20, max: 300, div: 100, desc: 'Толщина самой точки, отдельно от высоты полёта. 100 — как было, 300 — крупные капли, 20 — тонкая искра.' },
      { t: 'slider', key: 'spark_burst_count', label: 'Частиц во всплеске', min: 0, max: 800, div: 100, desc: 'Сколько быстрых частиц даёт один всплеск. 0 — всплесков нет совсем, только ровный фон, 100 — умеренно, 400 — плотный пучок, 800 — очень плотный (нужно много линий, иначе упрётесь в бюджет частиц).' },
      { t: 'slider', key: 'spark_burst_height', label: 'Высота всплеска', min: 2, max: 95, div: 100, desc: 'Потолок подъёма быстрых частиц, в процентах от высоты экрана. Медленный фон сюда не упирается — только всплеск. Больше разгона при этом не нужно: высокий всплеск всё равно не уйдёт за верхний край.' },
      { t: 'slider', key: 'spark_hover', label: 'Зависание на вершине', min: 0, max: 200, div: 100, desc: 'Сколько срока жизни частица проводит на вершине после подъёма. 0 — гаснет, как только долетела; 100 — половину срока висит наверху, фонтан пышнее, но под потолком стоит постоянное облако. 200 — ещё дольше.' },
      { t: 'slider', key: 'spark_bass_burst', label: 'Пучок на басу', min: 0, max: 100, div: 100, desc: 'На сильном ударе баса из одной точки вылетает пучок искр. 0 — выключено. Чем больше значение, тем сильнее и многочисленнее пучок. Ловится не бит вообще, а именно бас: удары по малому барабану его не запускают.' },
      { t: 'slider', key: 'spark_glow', label: 'Свечение искр', min: 0, max: 300, div: 100, desc: 'Ореол вокруг частицы, как drop-shadow в CSS. 0 — точки чистые и дешёвые, 100 — умеренный ореол, 300 — широкий светящийся шлейф.' },
      { t: 'slider', key: 'spark_glow_start', label: 'Свечение снизу', min: 0, max: 100, div: 100, desc: 'С какой высоты ореол начинает гореть. 0 — с самой базовой линии.' },
      { t: 'slider', key: 'spark_glow_end', label: 'Свечение сверху', min: 0, max: 100, div: 100, desc: 'На какой высоте ореол гаснет. 100 — горит до самой вершины. Если «снизу» поднять выше, чем «сверху», искры внизу будут чистыми и загорятся только у верха.' },
      { t: 'slider', key: 'color_dark', label: 'Темнота цвета', min: 0, max: 100, div: 100, desc: 'Насколько цвета могут быть тёмными — и фигура, и подсветка ПК. 100 — как есть, строго тем же цветом, что определился с экрана. 0 — всегда ярко и насыщенно: ушедшие в темноту цвета поднимаются и очищаются. Серый и белый остаются серым и белым: насыщать нечего, и ранняя версия превращала серый кадр в бирюзовый. На тёмном видео при 0 видно, при 100 почти гаснет.' },
      { t: 'slider', key: 'height_scale', label: 'Высота фигуры', min: 1, max: 200, div: 100, desc: 'Насколько высока фигура. У столбиков и волны — высота, у искр — высота полёта частиц. Раньше этот же ползунок задавал ещё и размер частицы; теперь размер отдельный.' },
      { t: 'slider', key: 'fig_size', label: 'Размер фигуры', min: 20, max: 250, div: 100, desc: 'Общий множитель размера: увеличивает фигуру целиком, не меняя её форму. Размер частиц у искр от него не зависит.' },
      { t: 'slider', key: 'viz_w', label: 'Ширина полосы', min: 20, max: 100, div: 100, desc: 'Ширина полосы, по которой бегут столбики. 100 — во весь экран, меньше — узкая полоса по центру.' },
      { t: 'slider', key: 'viz_x', label: 'Сдвиг по горизонтали', min: 0, max: 100, div: 100, desc: 'Сдвиг полосы влево-вправо. Заметно, когда полоса уже экрана.' },
      { t: 'slider', key: 'viz_y', label: 'Высота полосы над низом', min: 2, max: 100, div: 100, desc: 'Насколько высоко поднята полоса над нижним краем экрана. 100 — у самого низа, меньше — выше.' },
      { t: 'slider', key: 'fig_x', label: 'Центр круглой фигуры: X', min: 0, max: 100, div: 100, desc: 'Горизонтальный центр для кругов, дуг, спиралей и колец.' },
      { t: 'slider', key: 'fig_y', label: 'Центр круглой фигуры: Y', min: 5, max: 95, div: 100, desc: 'Вертикальный центр для кругов, дуг, спиралей и колец.' },
    ]},
    { g: 'Вид и прозрачность', desc: 'Яркость, заливка и сглаживание.', items: [
      { t: 'select', key: 'line_style', label: 'Линия', options: [
        ['Неон', 'neon'], ['Тонкая', 'thin'], ['Жирная', 'fat'], ['Двойная', 'double'],
        ['Пунктир', 'dash'], ['Градиент', 'gradient'], ['Точки', 'dotted'], ['Контур', 'outline'],
        ['Ступени', 'steps'], ['Яркое ядро', 'megaglow']], desc: 'Как нарисована линия: неон, тонкая, жирная, двойная, пунктир, градиент, точки, контур, ступени, яркое ядро. Видно на столбиках и на волне.' },
      { t: 'slider', key: 'line_width', label: 'Толщина линии', min: 1, max: 12, int: true, desc: 'Толщина линии в пикселях. Заметно на тонких фигурах и на контуре.' },
      { t: 'slider', key: 'alpha', label: 'Яркость линии', min: 40, max: 255, int: true, desc: 'Прозрачность самой линии, 40–255. На цвет картинки под собой не влияет.' },
      { t: 'slider', key: 'fill_alpha', label: 'Заливка (прозрачность)', min: 0, max: 180, int: true, desc: 'Плотность заливки фигуры. Работает у волны, заливки и ленты.' },
      { t: 'slider', key: 'wave_ceiling', label: 'Потолок амплитуды', min: 10, max: 100, div: 100, desc: 'Ограничивает, насколько высоко может подняться волна. На максимуме волна упирается в потолок и выше не идёт.' },
      { t: 'check', key: 'mirror', label: 'Зеркало вверх+вниз', desc: 'Рисовать фигуру симметрично вверх и вниз от базовой линии.' },
    ]},
    { g: 'Прозрачность и экран', desc: 'Насколько оверлей заметен и что он делает с картинкой под собой.', items: [
      { t: 'slider', key: 'overlay_opacity', label: 'Прозрачность оверлея', min: 10, max: 100, div: 100, desc: 'Насколько оверлей виден поверх картинки. 10 — еле заметен, 100 — непрозрачный.' },
      { t: 'slider', key: 'vignette', label: 'Виньетка (0 = выкл)', min: 0, max: 100, div: 100, desc: 'Затемнение по краям экрана, как в кино. 0 — выключено.' },
      { t: 'slider', key: 'bg_dim', label: 'Затемнение экрана', min: 0, max: 60, div: 100, desc: 'Постоянное затемнение поверх всей картинки, независимо от музыки.' },
      { t: 'slider', key: 'dim_pulse', label: 'Затемнение в такт', min: 0, max: 60, div: 100, desc: 'Дополнительное затемнение, которое качается в такт музыке. Складывается с постоянным затемнением.' },
      { t: 'slider', key: 'flash', label: 'Вспышка в такт', min: 0, max: 100, div: 100, desc: 'Вспышка цветом экрана поверх картинки в такт басу.' },
      { t: 'slider', key: 'shake', label: 'Тряска в такт', min: 0, max: 100, div: 100, desc: 'Тряска фигуры в такт музыке.' },
      { t: 'slider', key: 'zoom', label: 'Зум в такт', min: 0, max: 100, div: 100, desc: 'Фигура на мгновение увеличивается в такт музыке.' },
    ]},
    { g: 'Сила реакции', desc: 'Насколько фигура живо реагирует на бас.', items: [
      { t: 'slider', key: 'beat_strength', label: 'Сила пульса', min: 0, max: 100, div: 100, desc: 'Насколько сильно фигура пульсирует. Работает только вместе с галочкой «Пульс от баса».' },
      { t: 'check', key: 'beat_pulse', label: 'Пульс от баса', desc: 'Включает пульсацию фигуры по басу. Без этой галочки «Сила пульса» не действует.' },
    ]},
    { g: 'SYS — лупа всего экрана', desc: 'Через Magnification API: приближает и трясёт ВЕСЬ экран, а не только оверлей. Не работает в Exclusive Fullscreen. При выключении экран возвращается.', items: [
      { t: 'check', key: 'sys_fx', label: 'Приближение/тряска ВСЕГО экрана', desc: 'Через Magnification API приближает и трясёт ВЕСЬ экран, а не только оверлей. Не работает в Exclusive Fullscreen — игра в таком режиме экран не отдаёт.' },
      { t: 'select', key: 'sys_mode', label: 'Режим', options: [
        ['К центру', 'center'], ['По углам', 'corners']], desc: 'Куда наклоняется приближение: к центру экрана или по углам.' },
      { t: 'slider', key: 'sys_zoom', label: 'SYS-приближение', min: 0, max: 100, div: 100, desc: 'Сила приближения всего экрана.' },
      { t: 'slider', key: 'sys_shake', label: 'SYS-тряска', min: 0, max: 100, div: 100, desc: 'Сила тряски всего экрана.' },
    ]},
    { g: 'Запуск', desc: 'AuraFusion будет запускаться сам при входе в Windows. Браузер при этом не открывается — панель всегда на http://127.0.0.1:8765/, значок появляется в трее.', items: [
      { t: 'check', key: 'autostart', label: 'Запускать вместе с Windows', onchange: onAutostart, desc: 'При входе в Windows AuraFusion поднимется сам, без окна браузера. Панель всегда на http://127.0.0.1:8765/, значок появляется в трее.' },
    ]},
  ],

  rgb: [
    { g: 'Устройства подсветки', desc: 'OpenRGB-устройства и BLE-ленты — одним списком. ● включено, ○ выключено.', items: [
      { t: 'devs' },
      { t: 'buttons', items: [
        { t: 'button', label: '↻ Найти устройства', action: 'refresh_devices', toast: 'ищу устройства…', desc: 'Пересканировать USB и BLE. Нужно, если ленту подключили после запуска.' },
        { t: 'button', label: 'Тест цвета 4с', action: 'test_color', toast: 'тест 4с', desc: 'Включает подсветку на 4 секунды ярким белым — проверить, что лента вообще горит.' },
        { t: 'button', label: '⟳ Дёрнуть режим', action: 'kick_mode', toast: 'дёрнул режим', desc: 'Переключает режим контроллера в свежий — помогает, если лента зависла и не меняет цвет.' },
      ]},
      { t: 'slider', key: 'ble_max_fps', label: 'FPS BLE-ленты', min: 1, max: 60, int: true, desc: 'Потолок частоты обновления BLE-ленты. По GATT выше 20 раз в секунду не передать — лента всё равно не успеет, а USB будет занят.' },
      { t: 'slider', key: 'reassert_min', label: 'Пауза до переподключения (с)', min: 0, max: 60, int: true, desc: 'Минимальный интервал между повторными попытками вернуть BLE-ленту, если связь отвалилась. 0 — пробовать при каждом обновлении.' },
    ]},
    { g: 'Цвет', desc: 'Откуда ПК берёт цвет и как его доводить.', items: [
      { t: 'select', key: 'rgb_mode', label: 'Режим ПК', wide: true, options: [
        ['Микс: цвет экран + пульс музыка', 'mix'],
        ['Только экран', 'screen'], ['Только музыка', 'music']], desc: 'Откуда ПК берёт цвет: микс цвета экрана и пульса музыки, только экран или только музыка.' },
      { t: 'check', key: 'follow_screen', label: 'Авто-цвет под экран', onchange: onFollow, id: 'followRgb', desc: 'ПК берёт цвет с экрана. Выключено — берёт из «Статичного цвета».' },
      { t: 'slider', key: 'rgb_saturation', label: 'Насыщенность', min: 0, max: 150, div: 100, onchange: onSat, id: 'satRgb', desc: 'Насколько цвет ПК насыщенный. 100 — как на экране, больше — ярче и сочнее.' },
      { t: 'slider', key: 'color_dark', label: 'Темнота цвета', min: 0, max: 100, div: 100, desc: 'Ограничение снизу на то, насколько тёмным может быть цвет. 100 — строго цвет с экрана, 0 — всегда ярко и насыщенно. Тот же параметр, что и у фигуры на странице «Визуал».' },
      { t: 'slider', key: 'dark_threshold', label: 'Порог темноты (0 = выкл)', min: 0, max: 255, int: true, desc: 'Цвета темнее этого порога ПК не показывает — держится предыдущий. Поднимается, если ночью цвета уходят в темноту и подсветка гаснет.' },
      { t: 'slider', key: 'rgb_gain_r', label: 'Баланс R', min: 30, max: 150, div: 100, desc: 'Правка красного канала. Если подсветка уходит в розовый или зелёный, крутите здесь.' },
      { t: 'slider', key: 'rgb_gain_g', label: 'Баланс G', min: 30, max: 150, div: 100, desc: 'Правка зелёного канала.' },
      { t: 'slider', key: 'rgb_gain_b', label: 'Баланс B', min: 30, max: 150, div: 100, desc: 'Правка синего канала.' },
      { t: 'color', key: 'static_color', label: 'Статичный цвет (режим Музыка)', desc: 'Цвет, который ПК держит без экрана: в режиме «Только музыка» и когда авто-цвет выключен.' },
      { t: 'check', key: 'color_all', label: 'Один цвет на все устройства', desc: 'Все устройства показывают один и тот же цвет вместо своей раскраски по зонам.' },
      { t: 'check', key: 'rgb_enabled', label: 'Подсветка ПК включена', desc: 'Главный выключатель подсветки. Выключишь — погаснут все устройства сразу.' },
    ]},
    { g: 'Пульс от баса', desc: 'Яркость ПК качается музыкой: импульс, атака, затухание, границы.', items: [
      { t: 'slider', key: 'pulse_threshold', label: 'Порог импульса', min: 0, max: 50, div: 100, desc: 'С какой громкости баса начинается импульс. Ниже порога ПК только качается в такт, но не вспыхивает.' },
      { t: 'slider', key: 'pulse_gain', label: 'Усиление импульса', min: 20, max: 300, div: 100, desc: 'Насколько сильно вспыхивает ПК на импульсе.' },
      { t: 'slider', key: 'attack_s', label: 'Атака (с)', min: 1, max: 30, div: 100, desc: 'За сколько секунд яркость набирается. Меньше — вспышка резче.' },
      { t: 'slider', key: 'release_s', label: 'Затухание (с)', min: 5, max: 150, div: 100, desc: 'За сколько секунд яркость падает обратно. Меньше — вспышка короче.' },
      { t: 'slider', key: 'min_brightness', label: 'Мин. яркость', min: 0, max: 50, div: 100, desc: 'Ниже этого уровня подсветка не гаснет совсем: на тихой музыке ПК всё равно чуть светится.' },
      { t: 'slider', key: 'max_brightness', label: 'Макс. яркость', min: 20, max: 100, div: 100, desc: 'Потолок яркости. Ниже — подсветка никогда не выкручивается на полную.' },
      { t: 'slider', key: 'bass_boost', label: 'Усиление баса', min: 30, max: 300, div: 100, desc: 'Насколько сильно подсветка реагирует на низкие частоты.' },
      { t: 'slider', key: 'abs_floor', label: 'Порог тишины, %', min: 0, max: 100, div: 1000, scale: 100, desc: 'Ниже этой громкости музыка считается тишиной, и подсветка держит минимальную яркость, а не пульсирует. Поднимается, если подсветка дёргается на тихих местах.' },
      { t: 'slider', key: 'gamma', label: 'Кривая импульса', min: 10, max: 400, div: 100, desc: 'Форма вспышки. Ниже 1 — резкая и короткая, выше 1 — мягкая и широкая. При 1 — обычная плавная вспышка.' },
      { t: 'slider', key: 'idle_fps', label: 'FPS в простое', min: 1, max: 30, int: true, desc: 'Сколько раз в секунду обновляется подсветка, когда цвет не меняется. На тишине этого достаточно, а USB не греется.' },
    ]},
  ],

  amb: [
    { g: 'Замер цвета', desc: 'ПК берёт ТОТ ЖЕ сочный цвет, что и волна оверлея. Скрин 96×54 каждые 0.5 с → самый насыщенный цвет → плавно на железо.', items: [
      { t: 'select', key: 'color_monitor', label: 'Монитор замера', wide: true, dynamic: 'colormon',
        mini: refreshMonitors, options: [], desc: 'С какого монитора берётся цвет. По умолчанию — тот, где нарисован оверлей.' },
      { t: 'select', key: 'color_region', label: 'Область замера', wide: true, id: 'regionSel', onchange: onRegion,
        options: [['Весь экран', 'full'], ['Центр', 'center'], ['Верх', 'top'], ['Низ', 'bottom'], ['Своя...', 'custom']], desc: 'Какая часть экрана смотрится. «Центр» обычно лучше: в центре видео, по краям рамки и панель задач.' },
      { t: 'slider', key: 'color_rx', label: 'Область X, %', min: 0, max: 100, int: true, div: 100, scale: 100, onchange: onRegionCustom, desc: 'Своя область: отступ слева, в процентах ширины экрана.' },
      { t: 'slider', key: 'color_ry', label: 'Область Y, %', min: 0, max: 100, int: true, div: 100, scale: 100, onchange: onRegionCustom, desc: 'Своя область: отступ сверху, в процентах высоты экрана.' },
      { t: 'slider', key: 'color_rw', label: 'Область Ширина, %', min: 0, max: 100, int: true, div: 100, scale: 100, onchange: onRegionCustom, desc: 'Своя область: ширина в процентах ширины экрана.' },
      { t: 'slider', key: 'color_rh', label: 'Область Высота, %', min: 0, max: 100, int: true, div: 100, scale: 100, onchange: onRegionCustom, desc: 'Своя область: высота в процентах высоты экрана.' },
    ]},
    { g: 'Ambilight: экран → ПК', desc: 'Как быстро и какой цвет ПК принимает от экрана.', items: [
      { t: 'check', key: 'follow_screen', label: 'ПК следует за экраном', onchange: onFollow, id: 'followAmb', desc: 'ПК берёт цвет с экрана. Выключено — берёт из «Статичного цвета».' },
      { t: 'check', key: 'exclude_viz', label: 'Не учитывать волну при замере', desc: 'Вырезает из кадра саму волну оверлея. Иначе оверлей меряет сам себя и цвет гуляет.' },
      { t: 'check', key: 'panel_blocks', label: 'Панель во весь экран мешает замеру', desc: 'Выключено — цвет берётся с того, что на экране, включая саму панель. Включено — при панели во весь экран замер встаёт и держится последний цвет.' },
      { t: 'slider', key: 'color_sample_ms', label: 'Как часто мерить экран, мс', min: 33, max: 1000, int: true, desc: 'Сколько миллисекунд между снятиями кадра. Это и есть отклик: цвет не может смениться быстрее, чем придёт следующий кадр. Снимок стоит 33 мс, так что ниже 33 смысла нет, а чаще 60 съедает заметную долю процессора. Съёмка идёт в отдельном потоке, поэтому короткий интервал не рвёт отрисовку.' },
      { t: 'slider', key: 'auto_speed', label: 'Скорость подтяжки к экрану', min: 5, max: 100, div: 100, desc: 'Как быстро новый цвет догоняет старый. 100 — мгновенно, и на резких сценах цвет дёргается.' },
      { t: 'slider', key: 'rgb_saturation', label: 'Насыщенность', min: 0, max: 150, div: 100, onchange: onSat, id: 'satAmb', desc: 'Насколько цвет ПК насыщенный. 100 — как на экране, больше — ярче и сочнее.' },
      { t: 'slider', key: 'color_hold', label: 'Держать цвет, замеров', min: 1, max: 6, int: true, desc: 'Сколько замеров подряд новый оттенок должен удержаться, чтобы сменить цвет. Больше — меньше дёрганий, но реакция медленнее.' },
      { t: 'slider', key: 'color_speed', label: 'Скорость перехода цвета', min: 2, max: 50, div: 100, desc: 'Плавность перехода между цветами. 50 — половина пути за один кадр, меньше — мягче и дольше.' },
      { t: 'select', key: 'color_fx', label: 'Эффект смены', options: [
        ['Плавный', 'smooth'], ['Мгновенный (быстрее всех)', 'instant'], ['Радуга', 'rainbow'],
        ['Дыхание', 'breathe'], ['Пульс от баса', 'beat']], desc: 'Как ведёт себя цвет при смене: плавно, мгновенно, радугой, дыханием или пульсом от баса.' },
      { t: 'select', key: 'color_algo', label: 'Алгоритм цвета', options: [
        ['Сочный (точный оттенок)', 'juicy'], ['Средний (плавный)', 'average'], ['K-means (3 кластера)', 'kmeans']], desc: 'Как из кадра выбирается главный цвет. «Сочный» — точный оттенок, «Средний» — усреднённый и мягкий, «K-means» — три кластера, обычно точнее на смешанных сценах.' },
    ]},
  ],

  snd: [
    { g: 'Захват звука', desc: 'Общий движок pyaudiowpatch: один захват на оверлей и на подсветку.', items: [
      { t: 'select', key: 'audio_device', label: 'Источник', wide: true, dynamic: 'audio',
        onchange: onAudio, mini: refreshAudio, options: [['Системный вывод (авто)', '']], desc: 'Откуда берётся звук для анализа. Обычно это системный вывод, чтобы слышать ровно то, что играет.' },
      { t: 'buttons', items: [
        { t: 'button', label: '⟳ Перезапустить захват звука', action: 'restart_audio', toast: 'перезапускаю захват…', desc: 'Пересоздать захват системного вывода. Помогает, если звук перестал ловиться.' },
      ]},
      { t: 'hint', id: 'lblIn', text: 'Вход: —' },
      { t: 'check', key: 'demo', label: 'Демо-сигнал (если нет музыки)', desc: 'Если музыки нет, оверлей рисует на тестовом сигнале — так можно посмотреть настройки без музыки.' },
    ]},
    { g: 'Чувствительность', desc: 'Как громко и детально волна реагирует на музыку.', items: [
      { t: 'slider', key: 'sensitivity', label: 'Чувствительность', min: 20, max: 300, div: 100, desc: 'Общая громкость реакции. Поднимается, если волна еле шевелится.' },
      { t: 'slider', key: 'dynamics', label: 'Динамика', min: 100, max: 300, div: 100, desc: 'Насколько тихие звуки слышны в волне. Выше — больше деталей, но и больше шума.' },
      { t: 'slider', key: 'bass_gain', label: 'Бас', min: 0, max: 200, div: 100, desc: 'Усиление низких частот в спектре.' },
      { t: 'slider', key: 'mid_gain', label: 'Середина', min: 0, max: 250, div: 100, desc: 'Усиление средних частот в спектре.' },
      { t: 'slider', key: 'treble_gain', label: 'Верха', min: 0, max: 300, div: 100, desc: 'Усиление верхних частот в спектре.' },
      { t: 'slider', key: 'bars', label: 'Полос', min: 16, max: 128, int: true, desc: 'Сколько полос в спектре. Больше — детальнее, но и дороже по кадру.' },
      { t: 'check', key: 'auto_balance', label: 'Автобаланс (двигается вся волна)', desc: 'Каждая треть спектра приводится к своему недавнему пику, чтобы тихие частоты не пропадали. Без автобаланса громкая часть забивает остальное.' },
      { t: 'slider', key: 'avg_s', label: 'Усреднение громкости (с)', min: 5, max: 6000, div: 100, desc: 'За сколько секунд усредняется громкость — по этой средней считается, во сколько раз музыка громче обычного. Больше — «обычный» уровень отстаёт, и вспышки выходят неравномерными.' },
      { t: 'slider', key: 'chunk', label: 'Размер блока звука', min: 512, max: 4096, int: true, desc: 'Сколько звука берётся за раз. Больше — точнее низкие частоты, но и задержка больше. Менять имеет смысл только если что-то конкретное не работает.' },
    ]},
  ],

  sys: [
    { g: 'Оверлей', desc: 'Производительность и положение волны на рабочем столе.', items: [
      { t: 'select', key: 'overlay_fps', label: 'FPS оверлея', isInt: true, options: [
        ['30 (эко)', '30'], ['60 (баланс)', '60'], ['120 (плавно)', '120']], desc: 'Сколько кадров в секунду рисует оверлей. 60 — баланс, 120 — плавнее, но дороже по процессору.' },
      { t: 'select', key: 'monitor', label: 'Монитор оверлея', dynamic: 'ovmon',
        onchange: onOvMon, mini: refreshMonitors, options: [], desc: 'На каком мониторе нарисован оверлей.' },
      { t: 'slider', key: 'rgb_fps', label: 'FPS подсветки ПК', min: 1, max: 60, int: true, desc: 'Сколько раз в секунду обновляется подсветка. Выше — быстрее реагирует, но больше нагрузки на USB.' },
      { t: 'slider', key: 'startup_delay_s', label: 'Пауза перед автостартом (с)', min: 0, max: 180, int: true, desc: 'Сколько секунд подождать после старта Windows, прежде чем поднимать оверлей. Полезно, если система ещё грузится и звук ещё не готов.' },
    ]},
    { g: 'Сцены', desc: 'Наборы настроек под то, что вы делаете. Одно нажатие переносит сразу всё: режим цвета, силу эффектов, прозрачность оверлея.', items: [
      { t: 'scenes' },
    ]},
    { g: 'Оформление', desc: 'Цвета интерфейса. На саму подсветку и звук не влияет.', items: [
      { t: 'themes' },
    ]},
    { g: 'RGB-устройства', desc: 'Что подключено сейчас и какие типы контроллеров AuraFusion вообще понимает.', items: [
      { t: 'devs' },
      { t: 'catalog' },
    ]},
    { g: 'Поведение', desc: 'Не мешать играм и не грузить железо на старте.', items: [
      { t: 'check', key: 'click_through', label: 'Клики сквозь', onchange: v => onSysCheck('click_through', v), desc: 'Оверлей не перехватывает мышь: кликайте по нему как по обычному экрану.' },
      { t: 'check', key: 'always_on_top', label: 'Оверлей всегда поверх', onchange: v => onSysCheck('always_on_top', v), desc: 'Оверлей рисуется поверх всех окон, включая игры в обычном (не эксклюзивном) режиме.' },
      { t: 'check', key: 'avoid_taskbar', label: 'Не залезать на панель задач', onchange: v => onSysCheck('avoid_taskbar', v), desc: 'На автоскрытии панель выезжает под оверлей и кажется, что не поднялась.' },
      { t: 'check', key: 'auto_hide', label: 'Прятать волну в тишине', onchange: v => onSysCheck('auto_hide', v), desc: 'Когда музыки нет, волна исчезает, чтобы не мешать.' },
      { t: 'check', key: 'autostart', label: 'Запуск с Windows', onchange: v => onSysCheck('autostart', v), desc: 'При входе в Windows AuraFusion поднимется сам, без окна браузера. Панель всегда на http://127.0.0.1:8765/, значок появляется в трее.' },
      { t: 'check', key: 'autosave', label: 'Автосохранение', onchange: v => onSysCheck('autosave', v), desc: 'Сохранять настройки самим, без кнопки. Выключите, если меняете ползунки вручную и не хотите их терять.' },
    ]},
    { g: 'Профиль', desc: 'Настройки лежат в fusion_config.json и грузятся сами при старте.', items: [
      { t: 'buttons', items: [
        { t: 'button', label: '💾 Сохранить', action: 'save', toast: 'Сохранено ✓', desc: 'Записать настройки в файл. Обычно не нужно: при «Автосохранении» всё пишется само.' },
        { t: 'button', label: 'Выход', action: 'quit', toast: 'выхожу…', desc: 'Закрыть AuraFusion целиком: и оверлей, и подсветку. Панель в браузере останется, но станет бесполезной.' },
      ]},
      { t: 'hint', id: 'lblConf', text: 'Настройки грузятся сами при старте.' },
    ]},
  ],
};

const CTLS = {};
const findC = (page, key) => PAGES[page].flatMap(s => s.items || []).find(c => c.key === key);

/* ================= сборка ================= */
function buildControl(c) {
  if (c.t === 'themes') return mkThemeDD();
  if (c.t === 'scenes') return mkScenes();
  if (c.t === 'catalog') return mkCatalog();
  if (c.t === 'devs') {
    const box = el('div', 'devs');
    box.dataset.devs = '1';
    renderDevices();
    return box;
  }
  if (c.t === 'buttons') {
    const row = el('div', 'btnrow');
    for (const b of c.items) {
      // Пояснение живёт под кнопкой, в той же ячейке: отдельным текстом
      // оно читалось как «просто текст», и к какой кнопке относится —
      // неясно.
      const cell = el('div', 'btncell');
      const btn = el('button', 'btn' + (b.cls ? ' ' + b.cls : ''), b.label);
      btn.addEventListener('click', async () => {
        await api.act(b.action);
        toast(b.toast || 'готово');
      });
      cell.append(btn);
      if (b.desc) cell.append(el('div', 'btnhint', b.desc));
      row.append(cell);
    }
    return row;
  }
  if (c.t === 'hint') {
    const h = el('div', 'hint', c.text);
    h.style.gridColumn = '1 / -1';
    if (c.id) h.id = c.id;
    return h;
  }
  const node = { select: mkSelect, slider: mkSlider, check: mkCheck, color: mkColor }[c.t](c);
  c._node = node;
  if (c.id) { CTLS[c.id] = c; node.id = c.id; }
  if (c.span2 || c.wide) node.style.gridColumn = '1 / -1';
  // Пояснение под плиткой. Раньше desc был прописан в настройках, но
  // нигде не выводился: в панели было 106 кнопок и ноль объяснений, что
  // они делают. Текст всегда в DOM (он же подсказка при наведении),
  // а показывается он по общему переключателю «Пояснения» — иначе панель
  // превращается в стену текста.
  if (node && node.classList && node.classList.contains('tile') && c.desc) {
    const d = el('div', 'tdesc', c.desc);
    node.append(d);
    node.title = c.desc;
  }
  return node;
}

function buildPage(key) {
  const page = el('div', 'page');
  page.dataset.page = key;
  // Переключатель пояснений. По умолчанию включён: без него половина
  // настроек выглядит как загадка, а названия («Порог импульса»,
  // «Базовая линия») ничего не объясняют.
  const top = el('div', 'exprow');
  const sw = el('label', 'expwrap');
  const cbx = el('input');
  cbx.type = 'checkbox';
  cbx.checked = localStorage.getItem('aura.expl') !== '0';
  sw.append(cbx, el('span', null, 'Показывать пояснения'));
  cbx.addEventListener('change', () => {
    localStorage.setItem('aura.expl', cbx.checked ? '1' : '0');
    document.body.classList.toggle('expl', cbx.checked);
  });
  top.append(sw);
  page.append(top);
  document.body.classList.toggle('expl', cbx.checked);
  for (const sec of PAGES[key]) {
    const s = el('section', 'sec');
    const head = el('div', 'sechead');
    head.append(el('span', 'bar'), el('h3', null, sec.g));
    if (sec.desc) head.append(el('span', 'desc', sec.desc));
    s.append(head);
    const grid = el('div', 'grid');
    for (const c of sec.items || []) grid.append(buildControl(c));
    s.append(grid);
    sec._grid = grid;
    page.append(s);
  }
  return page;
}

function fillValues(key) {
  for (const sec of PAGES[key]) {
    for (const c of sec.items || []) {
      const val = c.key ? cur(c.key) : undefined;
      if (c.t === 'check') {
        c.el.checked = !!val;              // у input значение — это .checked, не .value
      } else if (c.setOptions) {
        c.setOptions(c.options);
        c.el.value = val;
      } else if (c.setValue) {
        c.setValue(val);
      }
      if (c.paint) c.paint();
    }
  }
}

/** Текущее значение с учётом личных настроек фигуры: сначала смотрим
    style_settings[стиль], потом общий конфиг. */
function cur(key) {
  const ov = (CFG.style_settings || {})[CFG.style || 'smooth'];
  return (ov && Object.prototype.hasOwnProperty.call(ov, key)) ? ov[key] : CFG[key];
}

function buildAll() {
  const host = $('#pages');
  host.innerHTML = '';
  Object.keys(PAGES).forEach(k => host.append(buildPage(k)));
  Object.keys(PAGES).forEach(fillValues);
}

function sync(what) {
  if (what === 'follow') {
    const v = !!CFG.follow_screen;
    ['followRgb', 'followAmb'].forEach(id => { if (CTLS[id]) CTLS[id].el.checked = v; });
  }
  if (what === 'sat') {
    ['satRgb', 'satAmb'].forEach(id => { if (CTLS[id]) CTLS[id].setValue(CFG.rgb_saturation); });
  }
  if (what === 'region' || what === 'regionSelect') {
    REG_KEYS.forEach(k => { const c = findC('amb', k); if (c && c.setValue) c.setValue(CFG[k]); });
    const rs = CTLS.regionSel;
    if (rs && rs.el) rs.el.value = CFG.color_region;
  }
  if (what === 'custom') {
    const c = PAGES.viz.flatMap(s => s.items || []).find(y => y.key === 'custom_color');
    if (c && c.paint) c.paint();
  }
}

function renderDevices() {
  document.querySelectorAll('[data-devs]').forEach(box => {
    box.innerHTML = '';
    if (!DEVICES.length) {
      box.append(el('div', 'empty', 'Устройства не найдены — нажмите «Найти устройства».'));
      return;
    }
    for (const d of DEVICES) {
      const w = el('div', 'dev' + (d.enabled ? ' on' : ''));
      w.append(el('span', 'led'), el('span', 'nm', d.name));
      if (d.ble) w.append(el('span', 'tag', 'BLE'));
      box.append(w);
    }
  });
}

/* ================= динамические списки ================= */
function monLabel(s) {
  return `${s.i + 1}: ${s.w}×${s.h}${s.primary ? ' (основной)' : ''}`;
}
function applyMonitors() {
  const cm = findC('amb', 'color_monitor');
  const om = findC('sys', 'monitor');
  if (cm) {
    cm.setOptions([['Где оверлей', '-1']].concat(MONITORS.map(s => [monLabel(s), String(s.i)])));
    setSafe(cm, CFG.color_monitor, 0);
  }
  if (om) {
    om.setOptions(MONITORS.map(s => [monLabel(s), String(s.i)]));
    setSafe(om, CFG.monitor, 0);
  }
}
function applyAudio() {
  const c = findC('snd', 'audio_device');
  if (!c) return;
  c.setOptions([['Системный вывод (авто)', '']].concat(AUDIO_NAMES.map(n => [n, n])));
  setSafe(c, CFG.audio_device, 0);
}
async function refreshMonitors() { MONITORS = (await api.monitors()).screens || []; applyMonitors(); }
async function refreshAudio() { AUDIO_NAMES = (await api.audio()).names || []; applyAudio(); }

/* ================= навигация ================= */
let current = 'viz';
function goto(key) {
  current = key;
  document.querySelectorAll('#nav .navbtn').forEach(b =>
    b.classList.toggle('active', b.dataset.page === key));
  document.querySelectorAll('#pages .page').forEach(p =>
    p.classList.toggle('on', p.dataset.page === key));
  if (key === 'amb' || key === 'sys') refreshMonitors();
  if (key === 'snd') refreshAudio();
  $('#pages').scrollTop = 0;
}

/* ================= живые данные ================= */
function applyState(s) {
  BG.update(s);
  $('#ringScreen').style.setProperty('--c', s.screen_hex);
  $('#ringPc').style.setProperty('--c', s.pc_hex);
  $('#hexScreen').textContent = s.screen_hex;
  $('#hexPc').textContent = s.pc_hex;

  const lvl = Math.max(0, Math.min(1, s.level));
  $('#meter').style.width = (lvl * 100).toFixed(1) + '%';
  $('#footbar').firstElementChild.style.width = (lvl * 100).toFixed(1) + '%';

  const mic = s.has_sig ? '\u{1F50A}' : '\u{1F507}';
  const st = $('#status');
  st.innerHTML = '';
  const a = document.createTextNode(`${mic} ${s.status} • ${s.audio_status}`);
  st.append(a);
  st.classList.toggle('err', !!s.error);
  if (s.error) st.append(document.createTextNode(` • ⚠ ${s.error}`));
  // Интерфейс закрывает экран — «цвет как на экране» молча берёт его самого.
  // Без этой подсказки выглядит так, будто сломан сам замер цвета.
  if (s.screen_color_blocked) {
    const w = document.createTextNode(
      ` • 👁 интерфейс закрывает экран (${Math.round((s.panel_cover || 0) * 100)}%), цвет с экрана не берётся`);
    st.append(w);
    st.classList.add('err');
  }

  const ss = $('#sidestatus');
  ss.textContent = `${s.status}\n${s.audio_status}` + (s.error ? `\n⚠ ${s.error}` : '')
    + (s.screen_color_blocked ? `\n👁 интерфейс закрывает экран (${Math.round((s.panel_cover || 0) * 100)}%)` : '');
  ss.classList.toggle('err', !!s.error || !!s.screen_color_blocked);

  const b = $('#btnRgb');
  b.textContent = s.running_rgb ? '❚❚ RGB СТОП' : '▶ RGB СТАРТ';
  b.classList.toggle('stop', !!s.running_rgb);
  $('#btnHide').textContent = s.overlay_visible ? 'Скрыть оверлей' : 'Показать оверлей';

  const li = $('#lblIn');
  if (li) {
    // показываем ФАКТИЧЕСКИЙ loopback, а не строку из конфига: имя в конфиге
    // может устареть, и тогда интерфейс врёт про то, что реально пишется
    const want = s.audio_device;
    const real = s.audio_active || '';
    const drifted = real && want && want !== 'авто' && !real.includes(want) && !want.includes(real);
    li.textContent = `Вход [${real || want}]: ${Math.round(lvl * 100)}% • ${s.has_sig ? 'звук' : 'тихо'}`
      + (drifted ? `  (в настройках: ${want})` : '');
  }

  if (s.saved_at && s.saved_at !== lastSavedAt) {
    lastSavedAt = s.saved_at;
    const lc = $('#lblConf');
    if (lc) { lc.textContent = 'Сохранено ✓'; lc.style.color = 'var(--accent)'; }
  }
  if (s.devices_total !== lastDevicesTotal) {
    lastDevicesTotal = s.devices_total;
    api.state().then(st => { DEVICES = st.devices || []; renderDevices(); });
  }
}

function startStream() {
  const es = new EventSource('/api/stream');
  es.onmessage = e => { try { applyState(JSON.parse(e.data)); } catch (_) { } };
}

/* ================= старт ================= */
(async function init() {
  document.querySelectorAll('#nav .navbtn').forEach(b =>
    b.addEventListener('click', () => goto(b.dataset.page)));
  $('#btnRgb').addEventListener('click', () => api.act('toggle_rgb'));
  $('#btnHide').addEventListener('click', () => api.act('toggle_overlay'));

  CFG = (await api.cfg()).config || {};
  try {
    const sp = await api.styles();
    STYLE_LIST = sp.styles || [];
    STYLE_KEYS = {};
    STYLE_OWN = {};
    for (const k of Object.keys(sp.keys || {})) STYLE_KEYS[k] = new Set(sp.keys[k]);
    for (const k of Object.keys(sp.own || {})) STYLE_OWN[k] = new Set(sp.own[k]);
  } catch (_) { /* сервер без профилей — показываем всё */ }
  BG.build();
  buildAll();
  // сцены — после сборки: renderScenes() ищет готовый блок в DOM, а во время
  // buildAll() он ещё не подвешен, и кнопок просто не появлялось
  await loadScenes();
  applyStyleProfile();
  applyTheme(CFG.web_theme || 'neon');   // после сборки: кнопка и галочки уже в DOM
  await refreshMonitors();
  await refreshAudio();
  const st = await api.state();
  DEVICES = st.devices || [];
  lastDevicesTotal = st.devices_total;
  renderDevices();
  applyState(st);
  goto('viz');
  startStream();

  // горячие клавиши: 1..5 — страницы, пробел — RGB
  document.addEventListener('keydown', e => {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    const keys = ['viz', 'amb', 'rgb', 'snd', 'sys'];
    if (e.key >= '1' && e.key <= '5') goto(keys[+e.key - 1]);
    if (e.code === 'Space') { e.preventDefault(); api.act('toggle_rgb'); }
  });

  // положение окна сообщаем сразу и дальше при переносе/изменении размера
  reportPanelGeom();
  window.addEventListener('resize', reportPanelGeom);
  window.addEventListener('focus', reportPanelGeom);
  setInterval(reportPanelGeom, 4000);
})();