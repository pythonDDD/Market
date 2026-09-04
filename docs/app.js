/* app.js — latest.json を読んで画面を描く
   データが無い項目は「データなし」と表示して隠さない。
   欠けているのに埋まっているように見えるのが一番危ないため。 */

/* グラフの中で使う色。CSSではなくここに置いているのは、SVGを文字列で組み立てているため。
   テーマを変えるときはこの1箇所を直せばよい。 */
const C = {
  grid: '#22323D', axis: '#3B5262', text: '#8CA3B2', ink: '#E4EFF2',
  sky: '#84D2F5', aqua: '#B0F1F0', cyan: '#00B4D8', navy: '#5E8FB5',
  up: '#3FD68C', down: '#FF6B6B', alert: '#FFEB3B',
  band10: '#1B3A46', band25: '#2B6E8C', line: '#B0F1F0',
  histIn: '#3E7E9E', histOut: '#22323D',
};

const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';

/* グラフに十字線と吹き出しを付ける共通処理。
   SVGを文字列で組んでいるので、描き終わったあとにDOMを掴んで手を加える形にしている。
   meta = { vbW, vbH, x0, x1, n, labels:[日付], top, bottom,
            series:[{name,color,values,fmt}], yOf(値)->座標 } */
function attachHover(svgId, meta) {
  const el = $(svgId);
  if (!el || !meta.n) return;
  const box = el.parentElement;
  box.style.position = 'relative';

  const cross = document.createElementNS(NS, 'line');
  cross.setAttribute('stroke', '#8CA3B2');
  cross.setAttribute('stroke-width', '1');
  cross.setAttribute('stroke-dasharray', '3 3');
  cross.setAttribute('y1', meta.top); cross.setAttribute('y2', meta.bottom);
  cross.style.display = 'none';
  el.appendChild(cross);

  const dots = meta.series.map(sr => {
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('r', '4'); c.setAttribute('fill', sr.color);
    c.setAttribute('stroke', '#0E161C'); c.setAttribute('stroke-width', '1.5');
    c.style.display = 'none'; el.appendChild(c); return c;
  });

  let tip = box.querySelector('.tip');
  if (!tip) { tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip); }

  const idxAt = ev => {
    const r = el.getBoundingClientRect();
    const cx = (ev.touches ? ev.touches[0].clientX : ev.clientX);
    const px = (cx - r.left) / r.width * meta.vbW;
    const t = (px - meta.x0) / (meta.x1 - meta.x0);
    return Math.max(0, Math.min(meta.n - 1, Math.round(t * (meta.n - 1))));
  };
  const show = ev => {
    const i = idxAt(ev);
    const x = meta.x0 + (meta.x1 - meta.x0) * (i / (meta.n - 1 || 1));
    cross.setAttribute('x1', x); cross.setAttribute('x2', x);
    cross.style.display = '';
    let rows = '';
    meta.series.forEach((sr, k) => {
      const v = sr.values[i];
      if (v === undefined || v === null) { dots[k].style.display = 'none'; return; }
      dots[k].setAttribute('cx', x); dots[k].setAttribute('cy', meta.yOf(v));
      dots[k].style.display = '';
      rows += `<div><i style="background:${sr.color}"></i>${sr.name}
        <b>${sr.fmt ? sr.fmt(v) : v}</b></div>`;
    });
    tip.innerHTML = `<div class="d">${meta.labels[i] ?? ''}</div>${rows}`;
    const r = el.getBoundingClientRect();
    const px = x / meta.vbW * r.width;
    tip.style.left = Math.min(Math.max(px, 8), r.width - 8) + 'px';
    tip.style.transform = px > r.width * 0.6 ? 'translate(-100%,0)' : 'translate(0,0)';
    tip.style.display = 'block';
  };
  const hide = () => { cross.style.display = 'none'; tip.style.display = 'none';
    dots.forEach(d => d.style.display = 'none'); };
  el.addEventListener('pointermove', show);
  el.addEventListener('pointerleave', hide);
  el.addEventListener('touchmove', e => { e.preventDefault(); show(e); }, { passive: false });
}
const F = (v, n = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—'
  : Number(v).toLocaleString('ja-JP', { minimumFractionDigits: n, maximumFractionDigits: n });
const SGN = (v, n = 2, suf = '%') => {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  // 四捨五入した結果が0なら符号を付けない。「−0.00pt」と出ていたため。
  const r = Number(Math.abs(v).toFixed(n));
  const sign = r === 0 ? '' : (v > 0 ? '+' : '−');
  return sign + F(Math.abs(v), n) + suf;
};
const CLS = v => v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
const T = (x, y, t, o = {}) => `<text x="${x}" y="${y}" font-size="${o.s || 11}" fill="${o.c || C.text}"
  text-anchor="${o.a || 'middle'}" font-weight="${o.w || 400}" font-family="Zen Kaku Gothic New,sans-serif">${t}</text>`;
const POLY = (p, c, w) => `<polyline points="${p}" fill="none" stroke="${c}" stroke-width="${w}"
  stroke-linecap="round" stroke-linejoin="round"/>`;
const svg = (id, s) => { const e = $(id); if (e) e.innerHTML = s; };
const empty = (id, msg) => svg(id, `<text x="50%" y="50%" text-anchor="middle" font-size="13"
  fill="${C.text}" font-family="Zen Kaku Gothic New,sans-serif">${msg}</text>`);

/* 相関の色。−1 は濃紺、0 は生成り、+1 は水色。配色カードのグラデーションに対応。 */
function corrColor(v) {
  // −1 は落ち着いた青、0 は背景に近い暗色、+1 は青緑。暗地でも順序が読めるようにしている。
  const stops = [[-1, [70, 110, 145]], [0, [30, 46, 57]], [1, [0, 180, 216]]];
  let a = stops[0], b = stops[1];
  if (v > 0) { a = stops[1]; b = stops[2]; }
  const t = (v - a[0]) / (b[0] - a[0]);
  return `rgb(${a[1].map((x, i) => Math.round(x + (b[1][i] - x) * t)).join(',')})`;
}

/* ========================================================================== */
async function main() {
  let D;
  try {
    const r = await fetch('data/latest.json?t=' + Date.now());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    D = await r.json();
  } catch (e) {
    $('banner').className = 'banner error';
    $('banner').textContent = 'データを読み込めませんでした（' + e.message + '）。更新処理が失敗しているか、まだ一度も実行されていません。';
    $('app').innerHTML = '<div class="loading">データがありません。</div>';
    return;
  }
  $('app').style.display = '';
  $('loading').style.display = 'none';
  render(D);
}

/* パーセンタイルが何を意味するかを日本語で言い換える。
   位置だけ示しても「で、それは珍しいのか」が伝わらないため。
   頻度は「およそ何営業日に1度か」で表す（1年＝252営業日）。 */
function PCT_MEAN(p) {
  if (p == null) return '';
  const tail = p >= 50 ? (100 - p) : p;      /* 端からの距離 */
  const dir  = p >= 50 ? '上' : '下';
  const once = tail <= 0 ? null : Math.round(100 / tail);
  const rare = tail >= 25 ? 'ごく普通の値動きです。'
             : tail >= 10 ? 'やや大きめですが、珍しくはありません。'
             : tail >= 5  ? '大きめの部類です。'
             : tail >= 1  ? 'かなり大きい部類です。'
             :              '過去2年でほとんど例のない大きさです。';
  /* 中央付近で「2日に1度」と書いても意味がないので、端に寄ったときだけ頻度を出す */
  return `つまり過去2年の中で${dir}から約${tail}%の位置です。`
       + (once && tail < 25 ? `これくらいの動きは、おおよそ${once}営業日に1度しか起きません。` : '')
       + rare;
}

function render(D) {
  const gen = new Date(D.generated_at);
  const ageMin = Math.round((Date.now() - gen.getTime()) / 60000);
  const h = D.health || {};

  /* --- 状態表示。古い・欠けている場合は必ず目立たせる --- */
  const stale = (h.stale || []).length;
  if (D.mode === 'mock') {
    $('banner').className = 'banner stale';
    $('banner').textContent = 'これは架空データによる表示です。実際の市場価格ではありません。';
  } else if (ageMin > 90) {
    /* しきい値は実測に合わせてある。GitHubの定期実行は混雑時に大量に間引かれ、
       15分ごとに設定していても実際は1〜2時間空くことがある（実測で確認済み）。
       30分や60分で警告を出すと、平常時にも鳴り続けて警告の意味を失う。 */
    $('banner').className = 'banner stale';
    $('banner').textContent = `データが ${ageMin} 分前のものです。`
      + `更新はGitHubの空き具合しだいで、1時間以上空くことがあります。`
      + `半日以上動いていなければ、更新処理が止まっています。`;
  } else if (stale > 0) {
    $('banner').className = 'banner stale';
    $('banner').textContent = `${stale} 銘柄の取得に失敗し、前回値のまま表示しています：`
      + h.stale.map(s => s.key).join(', ');
  }
  $('stamp').innerHTML =
    `<span><span class="dot${ageMin > 90 ? ' warn' : ''}"></span>最終更新 <b>${gen.toLocaleString('ja-JP')}</b>（${ageMin}分前）</span>`
    + `<span>取得 <b>${h.ok || 0}/${h.total || 0} 銘柄</b></span>`
    + `<span>処理 <b>${D.build_seconds || '—'}秒</b></span>`
    + `<span>更新間隔 <b>15分</b></span>`;

  drawScan(D);
  drawRisk(D);
  drawDecomp(D);
  drawCorr(D);
  drawBreaks(D);
  drawLag(D);
  drawCalendar(D);
  drawDist(D);
  drawAnalog(D);
  drawMatrix(D);
  drawFx(D);
  drawCrypto(D);
  drawTables(D);
  drawLog(D);
}

/* ---------- 今日の異常 ---------- */
function scanRows(list, scale) {
  return list.map(r => {
    const w = Math.min(Math.abs(r.z) / scale, 1) * 50, pos = r.z >= 0;
    const col = pos ? 'var(--up)' : 'var(--down)';
    return `<div class="row">
      <div class="lbl">${r.label}<small>${r.sub}${r.date ? ' ・ ' + r.date : ''}</small></div>
      <div class="zbar"><span style="background:${col};left:${pos ? 50 : 50 - w}%;width:${w}%"></span></div>
      <div class="val">${r.value}</div>
      <div class="z" style="color:${col}">${r.z >= 0 ? '+' : '−'}${Math.abs(r.z).toFixed(1)}σ</div>
    </div>`;
  }).join('');
}
function drawScan(D) {
  const s = D.scan || [], rel = D.scan_rel || [];
  if (!s.length && !rel.length) {
    $('scanRows').innerHTML = '<p class="note">スキャン結果がありません。</p>'; return;
  }
  const top = [...s, ...rel].sort((a, b) => Math.abs(b.z) - Math.abs(a.z))[0];
  $('scanLead').textContent = `いま最も普段と違うのは ${top.label} です。`;
  $('scanSub').textContent =
    `過去1年の分布と比べて ${Math.abs(top.z).toFixed(1)}σ 離れています。`
    + `±2σ を超える動きは、通常なら20営業日に1度程度しか起きません。`;
  // 値動きと関係の変化はスケールが違うので、混ぜずに分けて並べる
  $('scanRows').innerHTML =
    (s.length ? `<p class="subhead">値動きの異常</p>${scanRows(s, 4)}` : '')
    + (rel.length ? `<p class="subhead">関係の変化</p>${scanRows(rel, 3)}` : '');
}

/* ---------- リスク選好 ---------- */
function drawRisk(D) {
  const r = D.risk;
  if (!r) { empty('gauge', 'データなし'); $('riskVal').textContent = '—'; return; }
  const cx = 160, cy = 150, R = 110, val = r.value;
  const ang = v => Math.PI * (1 - (v + 100) / 200);
  let g = '';
  for (let i = 0; i < 40; i++) {
    const v0 = -100 + i * 5, a0 = ang(v0), a1 = ang(v0 + 5), t = (v0 + 100) / 200;
    // 左（リスクオフ）は沈んだ青、右（リスクオン）は青緑へ
    const c = `rgb(${Math.round(70 - 70 * t)},${Math.round(110 + 70 * t)},${Math.round(145 + 71 * t)})`;
    g += `<path d="M ${cx + Math.cos(a0) * R} ${cy - Math.sin(a0) * R}
      A ${R} ${R} 0 0 1 ${cx + Math.cos(a1) * R} ${cy - Math.sin(a1) * R}
      L ${cx + Math.cos(a1) * (R - 20)} ${cy - Math.sin(a1) * (R - 20)}
      A ${R - 20} ${R - 20} 0 0 0 ${cx + Math.cos(a0) * (R - 20)} ${cy - Math.sin(a0) * (R - 20)} Z"
      fill="${c}" opacity=".9"/>`;
  }
  const a = ang(val);
  g += `<line x1="${cx}" y1="${cy}" x2="${cx + Math.cos(a) * (R - 6)}" y2="${cy - Math.sin(a) * (R - 6)}"
      stroke="${C.ink}" stroke-width="3.5" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="7" fill="${C.ink}"/>`;
  g += T(cx - R + 4, cy + 22, 'リスクオフ') + T(cx + R - 4, cy + 22, 'リスクオン') + T(cx, cy + 22, '中立');
  svg('gauge', g);
  $('riskVal').textContent = (val > 0 ? '+' : '') + val.toFixed(0);
  $('riskCap').textContent = val > 30 ? 'リスクオン' : val < -30 ? 'リスクオフ' : '中立圏';
  $('riskComp').innerHTML = r.components.map(c =>
    `<div class="comp"><span title="${c.desc}">${c.name}</span>
     <b class="${CLS(c.z)}">${SGN(c.z, 2, 'σ')}</b></div>`).join('');

  const S = D.risk_series || [];
  if (S.length < 5) { empty('riskHist', 'データが足りません'); return; }
  const W = 460, H = 210, p = { t: 14, r: 12, b: 26, l: 34 }, iw = W - p.l - p.r, ih = H - p.t - p.b;
  const x = i => p.l + i / (S.length - 1) * iw, y = q => p.t + ((100 - q) / 200) * ih;
  let gg = '';
  [-100, -50, 0, 50, 100].forEach(q => {
    gg += `<line x1="${p.l}" y1="${y(q)}" x2="${W - p.r}" y2="${y(q)}" stroke="${q ? C.grid : C.axis}"/>`
      + T(p.l - 6, y(q) + 4, q, { a: 'end' });
  });
  gg += POLY(S.map((v, i) => `${x(i).toFixed(1)},${y(v[1]).toFixed(1)}`).join(' '), C.sky, 2);
  gg += `<circle cx="${x(S.length - 1)}" cy="${y(S[S.length - 1][1])}" r="4" fill="${C.sky}"/>`;
  gg += T(p.l, H - 8, S[0][0], { a: 'start', s: 10 }) + T(W - p.r, H - 8, S[S.length - 1][0], { a: 'end', s: 10 });
  svg('riskHist', gg);
  attachHover('riskHist', {
    vbW: W, x0: p.l, x1: W - p.r, n: S.length, top: p.t, bottom: p.t + ih,
    labels: S.map(v => v[0]), yOf: y,
    series: [{ name: 'リスク選好', color: C.sky, values: S.map(v => v[1]),
               fmt: v => (v > 0 ? '+' : '') + v.toFixed(0) }],
  });
  attachHover('riskHist', {
    vbW: W, x0: p.l, x1: W - p.r, n: S.length, top: p.t, bottom: p.t + ih,
    labels: S.map(v => v[0]), yOf: y,
    series: [{ name: 'リスク選好', color: C.sky, values: S.map(v => v[1]),
               fmt: v => (v > 0 ? '+' : '') + v.toFixed(0) }]
  });
}

/* ---------- BTCの分解 ---------- */
function drawDecomp(D) {
  const d = D.decomp;
  if (!d) { $('decompBox').innerHTML = '<p class="note">計算に必要なデータが足りません。</p>'; empty('betaChart', 'データなし'); return; }
  const ex = Math.abs(d.explained), id = Math.abs(d.idiosyncratic), tot = ex + id || 1;
  // 説明分と固有分は「役割」で色を分ける。符号で決めると、固有分がマイナスのとき
  // 説明分と同系色になって見分けがつかなくなる（実際にそうなっていた）。
  /* 2本とも符号で色を分ける。片方だけ水色だと、プラスなのかマイナスなのかが読めない。
     区別は凡例の文字と、帯の濃さ（説明できる分は淡く）で付ける。 */
  const cIdio = d.idiosyncratic >= 0 ? 'var(--up)' : 'var(--down)';
  const cExp  = d.explained     >= 0 ? 'var(--up)' : 'var(--down)';
  $('decompBox').innerHTML =
    `<p style="margin:0;font-size:13.5px;color:var(--muted)">${d.date} のビットコイン
      <b class="${CLS(d.btc)}" style="font-size:19px">${SGN(d.btc)}</b></p>
     <div class="decomp">
       <div style="width:${(ex / tot * 100).toFixed(1)}%;background:${cExp};opacity:.55">${SGN(d.explained)}</div>
       <div style="width:${(id / tot * 100).toFixed(1)}%;background:${cIdio}">${SGN(d.idiosyncratic)}</div>
     </div>
     <div class="dlg"><span><i style="background:${cExp};opacity:.55"></i>株式で説明できる分</span>
       <span><i style="background:${cIdio}"></i>ビットコイン固有の動き</span></div>
     <div class="formula">
       <div class="fml-eq">BTC<sub>日次</sub> = α + β × SPX<sub>日次</sub> + 誤差</div>
       <div class="fml-num">= ${d.alpha >= 0 ? '+' : '−'}${Math.abs(d.alpha * 100).toFixed(3)}%
         ${d.beta >= 0 ? '+' : '−'} ${Math.abs(d.beta).toFixed(2)} × SPX<sub>日次</sub></div>
       <div class="fml-note">最小二乗法・過去252営業日・日次リターン</div>
     </div>
     <p class="note">αは「S&amp;P500が動かなかった日にビットコインが平均どれだけ動くか」、
       βは「S&amp;P500が1%動いたときビットコインが何%動くか」です。
       決定係数 R²=${F(d.r2, 2)}。
       つまりこの期間のビットコインの値動きのうち、S&amp;P500で説明できるのは
       <b>${(d.r2 * 100).toFixed(0)}%</b> だけです。</p>
     <p class="note">この日付は、ビットコインとS&amp;P500の<b>両方に終値がある直近の日</b>です。
       ビットコインは土日も動くため、下の「分布の中の位置」に出る日付とはずれることがあります。</p>`;

  const B = d.beta_series || [];
  if (B.length < 5) { empty('betaChart', 'データが足りません'); return; }
  const W = 460, H = 210, p = { t: 14, r: 12, b: 26, l: 36 }, iw = W - p.l - p.r, ih = H - p.t - p.b;
  const vals = B.map(v => v[1]);
  const lo = Math.min(-0.4, ...vals) - 0.1, hi = Math.max(1.2, ...vals) + 0.1;
  const x = i => p.l + i / (B.length - 1) * iw, y = q => p.t + ((hi - q) / (hi - lo)) * ih;
  let g = '';
  for (let q = Math.ceil(lo * 2) / 2; q <= hi; q += 0.5) {
    g += `<line x1="${p.l}" y1="${y(q)}" x2="${W - p.r}" y2="${y(q)}" stroke="${Math.abs(q) < 1e-9 ? C.axis : C.grid}"/>`
      + T(p.l - 6, y(q) + 4, q.toFixed(1), { a: 'end' });
  }
  g += POLY(B.map((v, i) => `${x(i).toFixed(1)},${y(v[1]).toFixed(1)}`).join(' '), C.cyan, 2.2);
  g += `<circle cx="${x(B.length - 1)}" cy="${y(vals[vals.length - 1])}" r="4.5" fill="${C.cyan}"/>`
    + T(x(B.length - 1) - 9, y(vals[vals.length - 1]) - 11, F(vals[vals.length - 1], 2),
      { a: 'end', c: C.ink, w: 700, s: 12.5 });
  svg('betaChart', g);
  attachHover('betaChart', {
    vbW: W, x0: p.l, x1: W - p.r, n: B.length, top: p.t, bottom: p.t + ih,
    labels: B.map(v => v[0]), yOf: y,
    series: [{ name: '90日β', color: C.cyan, values: vals, fmt: v => v.toFixed(2) }]
  });
  $('betaNote').innerHTML =
    `<b>90日</b>ローリングβの推移。1.0を超えると株式より大きく振れ、0付近では株式と
     切り離れて動いています。左の本文のβ（${F(d.beta, 2)}）は<b>252日</b>で推定した値なので、
     このグラフの末尾（${F(vals[vals.length - 1], 2)}）とは窓の長さが違うぶん一致しません。`;
}

/* ---------- ローリング相関 ---------- */
const CORR_STYLE = { SPX: ['S&P500', '#B0F1F0', 2.6], NDX: ['ナスダック100', '#84D2F5', 1.8],
  GOLD: ['金', '#00B4D8', 1.8], DXY: ['ドル指数', '#3FD68C', 1.6] };
function drawCorr(D) {
  const R = D.rolling_corr || {};
  const series = Object.entries(R).filter(([k, v]) => v && v.length > 5);
  if (!series.length) { empty('corrChart', 'データなし'); return; }
  const N = Math.max(...series.map(([, v]) => v.length));
  const W = 900, H = 300, p = { t: 16, r: 16, b: 34, l: 44 }, iw = W - p.l - p.r, ih = H - p.t - p.b;
  const x = i => p.l + i / (N - 1) * iw, y = v => p.t + ((1 - v) / 2) * ih;
  let g = '';
  [-1, -.5, 0, .5, 1].forEach(v => {
    g += `<line x1="${p.l}" y1="${y(v)}" x2="${W - p.r}" y2="${y(v)}" stroke="${v ? C.grid : C.axis}"/>`
      + T(p.l - 9, y(v) + 4, (v > 0 ? '+' : '') + v.toFixed(1), { a: 'end' });
  });
  const spx = R.SPX || [];
  const fn = spx.findIndex(v => v[1] < 0);
  if (fn >= 0) g += `<rect x="${x(fn)}" y="${p.t}" width="${W - p.r - x(fn)}" height="${ih}" fill="${C.alert}" opacity=".10"/>`;
  series.sort((a, b) => CORR_STYLE[a[0]][2] - CORR_STYLE[b[0]][2]).forEach(([k, v]) => {
    const off = N - v.length;
    g += POLY(v.map((d, i) => `${x(i + off).toFixed(1)},${y(d[1]).toFixed(1)}`).join(' '),
      CORR_STYLE[k][1], CORR_STYLE[k][2]);
  });
  if (spx.length) {
    const last = spx[spx.length - 1];
    g += `<circle cx="${x(N - 1)}" cy="${y(last[1])}" r="4.5" fill="${C.aqua}"/>`
      + T(x(N - 1) - 9, y(last[1]) - 12, SGN(last[1], 2, ''), { a: 'end', c: C.ink, w: 700, s: 12.5 });
    g += T(p.l, H - 10, spx[0][0], { a: 'start', s: 10 }) + T(W - p.r, H - 10, last[0], { a: 'end', s: 10 });
  }
  // 標本30個の相関には大きな誤差がある。その幅を右端に帯で描く。
  const ci = D.corr_ci;
  if (ci) {
    g += `<rect x="${x(N - 1) - 5}" y="${y(ci.hi)}" width="10"
      height="${Math.max(y(ci.lo) - y(ci.hi), 2)}" rx="4" fill="${C.aqua}" opacity=".22"/>`;
  }
  svg('corrChart', g);
  const off = k => N - (R[k] || []).length;
  attachHover('corrChart', {
    vbW: W, x0: p.l, x1: W - p.r, n: N, top: p.t, bottom: p.t + ih,
    labels: Array.from({ length: N }, (_, i) => (R.SPX && R.SPX[i - off('SPX')]) ?
      R.SPX[i - off('SPX')][0] : ''),
    yOf: y,
    series: Object.entries(CORR_STYLE).filter(([k]) => R[k]).map(([k, st]) => ({
      name: 'BTC × ' + st[0], color: st[1],
      values: Array.from({ length: N }, (_, i) => {
        const a = R[k][i - off(k)]; return a ? a[1] : null; }),
      fmt: v => (v > 0 ? '+' : '') + v.toFixed(2)
    }))
  });
  $('corrLegend').innerHTML = Object.entries(CORR_STYLE).map(([k, s]) =>
    `<span><i style="background:${s[1]}"></i>BTC × ${s[0]}</span>`).join('')
    + (ci ? `<span style="color:var(--aqua)">直近値の95%誤差幅 ${SGN(ci.lo, 2, '')} 〜 ${SGN(ci.hi, 2, '')}</span>` : '');
  $('corrNote').innerHTML = ci
    ? `黄色の網掛けは、BTC×S&amp;P500の相関が最後に0を割ってから現在までの期間。
       グラフに触れると、その日の4本すべての値が出ます。<br>
       <b>右端の帯は誤差の幅です。</b>直近の相関 ${SGN(ci.r, 2, '')} は標本${ci.n}個から
       計算したもので、95%の幅は ${SGN(ci.lo, 2, '')} 〜 ${SGN(ci.hi, 2, '')}。
       ${ci.lo < 0 && ci.hi > 0 ? 'この幅は0をまたいでいるので、<b>「連動していない」以上のことは言えません</b>。'
        : 'この幅は0をまたいでいないので、符号については意味があると言えます。'}`
    : 'グラフに触れると、その日の値が出ます。';
}

/* ---------- 相関の壊れ度 ---------- */
function drawBreaks(D) {
  const B = D.corr_breaks || [];
  const nm = k => (D.matrix && D.matrix.keys.includes(k))
    ? D.matrix.names[D.matrix.keys.indexOf(k)] : k;
  if (!B.length) { $('breaksRows').innerHTML = '<p class="note">データなし</p>'; return; }
  $('breaksRows').innerHTML = B.map(r => {
    const w = Math.min(Math.abs(r.gap) / 1.5, 1) * 50, pos = r.gap >= 0;
    return `<div class="row">
      <div class="lbl">${nm(r.a)} × ${nm(r.b)}<small>直近30日と1年の差</small></div>
      <div class="zbar"><span style="background:${pos ? 'var(--up)' : 'var(--down)'};
        left:${pos ? 50 : 50 - w}%;width:${w}%"></span></div>
      <div class="val">30日 ${SGN(r.c30, 2, '')} / 1年 ${SGN(r.c252, 2, '')}</div>
      <div class="z ${CLS(r.gap)}">${SGN(r.gap, 2, '')}</div></div>`;
  }).join('');
}

/* ---------- リード・ラグ ---------- */
function drawLag(D) {
  const L = D.lead_lag || {};
  const names = { SPX: 'S&P500 → ビットコイン', DXY: 'ドル指数 → ビットコイン', GOLD: '金 → ビットコイン' };
  const box = $('lagBox');
  box.innerHTML = '';
  Object.entries(L).forEach(([k, vals]) => {
    const id = 'lag_' + k;
    const usable = vals.filter(v => v[1] !== null);
    if (!usable.length) return;
    const best = usable.reduce((a, b) => Math.abs(b[1]) > Math.abs(a[1]) ? b : a);
    box.insertAdjacentHTML('beforeend',
      `<div class="card"><svg id="${id}" viewBox="0 0 460 230"></svg>
       <p class="note">相関が最も強いのは <b>${best[0] > 0 ? '+' : ''}${best[0]}日</b>（${SGN(best[1], 2, '')}）。
       ${best[0] > 0 ? `${names[k].split(' → ')[0]}の動きが ${best[0]} 日遅れてビットコインに現れています。`
        : best[0] < 0 ? 'ビットコインの方が先行しています。' : '同じ日に動いています。'}</p></div>`);
    const W = 460, H = 230, p = { t: 26, r: 14, b: 40, l: 38 }, iw = W - p.l - p.r, ih = H - p.t - p.b;
    const mx = Math.max(0.2, ...usable.map(v => Math.abs(v[1]))) * 1.15;
    const bw = iw / vals.length, y = v => p.t + ((mx - v) / (2 * mx)) * ih;
    let g = T(W / 2, 15, names[k], { c: C.ink, w: 700, s: 13 });
    [-mx, -mx / 2, 0, mx / 2, mx].forEach(v => {
      g += `<line x1="${p.l}" y1="${y(v)}" x2="${W - p.r}" y2="${y(v)}" stroke="${Math.abs(v) < 1e-9 ? C.axis : C.grid}"/>`
        + T(p.l - 6, y(v) + 4, v.toFixed(2), { a: 'end', s: 10 });
    });
    vals.forEach(([lag, v], i) => {
      if (v === null) return;
      const cx = p.l + bw * i + bw * 0.15, w = bw * 0.7;
      const on = lag === best[0];
      g += `<rect x="${cx}" y="${v >= 0 ? y(v) : y(0)}" width="${w}"
        height="${Math.max(Math.abs(y(v) - y(0)), 1)}" rx="2"
        fill="${on ? C.cyan : (v >= 0 ? C.up : C.down)}" opacity="${on ? 1 : .75}"/>`;
      if (lag % 2 === 0) g += T(cx + w / 2, H - 22, lag > 0 ? '+' + lag : lag, { s: 10 });
    });
    g += T(W / 2, H - 6, '← ビットコインが先行　　ラグ（日）　　ビットコインが後追い →', { s: 10.5 });
    svg(id, g);
    attachHover(id, {
      vbW: W, x0: p.l + bw * 0.5, x1: p.l + bw * (vals.length - 0.5),
      n: vals.length, top: p.t, bottom: p.t + ih,
      labels: vals.map(([lag]) => lag === 0 ? '同じ日' :
        (lag > 0 ? `${names[k].split(' → ')[0]}が ${lag} 日先行` : `ビットコインが ${-lag} 日先行`)),
      yOf: y,
      series: [{ name: '相関', color: C.cyan, values: vals.map(v => v[1]),
                 fmt: v => (v > 0 ? '+' : '') + v.toFixed(3) }],
    });
  });
  if (!box.innerHTML) box.innerHTML = '<p class="note">データなし</p>';
}

/* ---------- 曜日・月 ---------- */
const DOW = ['月', '火', '水', '木', '金', '土', '日'];
const MON = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
function heatColor(v, scale) {
  const t = Math.max(-1, Math.min(1, v / scale));
  /* プラスは緑（--up 63,214,140）、マイナスは赤（--down 255,107,107）。
     サイト全体の配色と揃えてある。 */
  return t >= 0 ? `rgba(63,214,140,${(0.15 + t * .55).toFixed(2)})`
                : `rgba(255,107,107,${(0.15 - t * .55).toFixed(2)})`;
}
function drawCalendar(D) {
  const C = D.calendar || {};
  const nm = { BTC: 'ビットコイン', SPX: 'S&P500', N225: '日経平均', GOLD: '金' };
  ['dow', 'month'].forEach(kind => {
    const cols = kind === 'dow' ? DOW : MON;
    const rows = Object.entries(C).filter(([, v]) => v[kind]);
    if (!rows.length) { $(kind + 'Table').innerHTML = ''; return; }
    // 色の振り切れ幅は、その表に出てくる値の大きさから決める
    const all = rows.flatMap(([, v]) => v[kind].map(c => Math.abs(c.mean || 0)));
    const scale = Math.max(0.05, ...all) * 0.9;
    let h = '<tr><th></th>' + cols.map(c => `<th>${c}</th>`).join('') + '</tr>';
    rows.forEach(([k, v]) => {
      h += `<tr><th class="row">${nm[k] || k}</th>` + v[kind].map(c => {
        if (!c.n) return '<td><div class="cell" style="background:var(--surface2);color:var(--muted)"><b>—</b><em>休場</em></div></td>';
        // t値が小さい＝偶然の範囲。点線で囲って「弱い」と分かるようにする
        const weak = c.tstat === null || Math.abs(c.tstat) < 2;
        return `<td><div class="cell${weak ? ' weak' : ''}" style="background:${heatColor(c.mean, scale)}">
          <b>${SGN(c.mean, 2)}</b><em>勝率${c.winrate}% / ${c.n}回</em></div></td>`;
      }).join('') + '</tr>';
    });
    $(kind + 'Table').innerHTML = h;
  });
}

/* ---------- 分布 ---------- */
function drawDist(D) {
  const Dd = D.distribution || {};
  const nm = { BTC: 'ビットコイン 日次リターン', SPX: 'S&P500 日次リターン', VIX: 'VIX 日次変化' };
  const box = $('distBox');
  box.innerHTML = '';
  Object.entries(Dd).forEach(([k, d]) => {
    const id = 'hist_' + k;
    box.insertAdjacentHTML('beforeend',
      `<div class="card"><svg id="${id}" viewBox="0 0 460 220"></svg>
       <p class="note">${nm[k] || k}${d.date ? `（${d.date}）` : ''} の直近値は
       <b>${SGN(d.now)}</b>。過去2年の分布で下から
       <b>${F(d.pctile, 0)} パーセンタイル</b>です。${PCT_MEAN(d.pctile)}</p></div>`);
    const V = d.values, B = 41;
    const lo = Math.min(...V), hi = Math.max(...V), bw = (hi - lo) / B || 1;
    const bins = new Array(B).fill(0);
    V.forEach(v => { const i = Math.min(B - 1, Math.max(0, Math.floor((v - lo) / bw))); bins[i]++; });
    const W = 460, H = 220, p = { t: 18, r: 26, b: 38, l: 26 }, iw = W - p.l - p.r, ih = H - p.t - p.b;
    const mxc = Math.max(...bins);
    const x = v => p.l + ((v - lo) / (hi - lo)) * iw, y = c => p.t + ih - (c / mxc) * ih;
    let g = '';
    bins.forEach((c, i) => {
      const v = lo + bw * (i + .5);
      g += `<rect x="${x(lo + bw * i)}" y="${y(c)}" width="${iw / B - 1}"
        height="${p.t + ih - y(c)}" rx="1.5" fill="${v < d.now ? C.histIn : C.histOut}"/>`;
    });
    g += `<line x1="${x(d.now)}" y1="${p.t - 4}" x2="${x(d.now)}" y2="${p.t + ih + 6}" stroke="${C.up}" stroke-width="2.5"/>`
      + T(x(d.now), p.t - 8, '直近', { c: C.up, w: 700, s: 12 });
    [lo, (lo + hi) / 2, hi].forEach(v => g += T(x(v), H - 18, SGN(v, 1)));
    svg(id, g);
  });
  if (!box.innerHTML) box.innerHTML = '<p class="note">データなし</p>';
}

/* ---------- 類似局面 ---------- */
function drawAnalog(D) {
  const a = D.analog;
  const note = $('analogNote');
  if (!a) {
    $('analogText').innerHTML = '<p class="note">該当する過去の事例が足りず、計算できませんでした。</p>';
    empty('fan', 'データなし'); note.innerHTML = ''; return;
  }
  const c = a.condition || {};
  const need = c.min_clusters || 15;
  $('analogText').innerHTML =
    `<p style="margin:0 0 4px;font-size:13.5px">条件：<b>${c.level || '—'}</b><br>
      <span style="color:var(--muted)">現在の状態 ─ BTC×S&amp;P500の30日相関 ${SGN(c.corr30, 2, '')}、
      VIX ${F(c.vix, 1)}、20日移動平均を${c.below_ma20 ? '下回る' : '上回る'}（${c.as_of}時点）</span></p>
     <p style="margin:0 0 18px;font-size:13.5px;color:var(--muted)">
      過去に該当したのは <b style="color:var(--ink)">${a.n_matches}日</b>。
      連続した日をひとまとまりと数えると
      <b style="color:var(--ink)">${a.n_independent}局面</b>です。</p>`;

  // 独立した局面が少ないときはグラフを描かない。
  // 数局面から引いた分位点の帯は、統計に見えるだけで中身が無く、誤読の害の方が大きい。
  if (!c.reliable) {
    empty('fan', `独立した局面が ${a.n_independent} 回しかないため、分布は表示しません`);
    note.innerHTML =
      `<b>表示を止めています。</b>この分布を描くには独立した局面が ${need} 回は必要ですが、
       いまは ${a.n_independent} 回しかありません。条件を段階的に緩めても足りなかったということです。
       少数の事例から引いたパーセンタイルは、統計のように見えて中身がありません。
       条件が変われば自動的に表示に戻ります。`;
    return;
  }

  const b = a.bands, N = b['50'].length;
  const W = 900, H = 300, p = { t: 18, r: 66, b: 34, l: 48 }, iw = W - p.l - p.r, ih = H - p.t - p.b;
  const all = Object.values(b).flat();
  const lo = Math.min(...all) * 1.1, hi = Math.max(...all) * 1.1;
  const x = i => p.l + i / (N - 1) * iw, y = v => p.t + ((hi - v) / (hi - lo)) * ih;
  const band = (A1, A2, col) => `<path d="M ${A1.map((v, i) => `${x(i)},${y(v)}`).join(' L ')}
    L ${A2.map((v, i) => `${x(i)},${y(v)}`).reverse().join(' L ')} Z" fill="${col}"/>`;
  let g = '';
  const step = (hi - lo) / 4;
  for (let i = 0; i <= 4; i++) {
    const v = lo + step * i;
    g += `<line x1="${p.l}" y1="${y(v)}" x2="${W - p.r}" y2="${y(v)}" stroke="${C.grid}"/>`
      + T(p.l - 8, y(v) + 4, SGN(v, 0), { a: 'end' });
  }
  g += `<line x1="${p.l}" y1="${y(0)}" x2="${W - p.r}" y2="${y(0)}" stroke="${C.axis}"/>`;
  [0, 5, 10, 15, 20].filter(i => i < N).forEach(i => g += T(x(i), H - 12, `${i}日後`));
  // 縦軸が何を表しているかを図の中に書く。相関ではなく価格の累積リターン。
  g += `<text x="14" y="${p.t + ih / 2}" font-size="11" fill="${C.text}"
    transform="rotate(-90 14 ${p.t + ih / 2})" text-anchor="middle"
    font-family="Zen Kaku Gothic New,sans-serif">ビットコインの累積リターン</text>`;
  g += band(b['10'], b['90'], C.band10) + band(b['25'], b['75'], C.band25);
  g += POLY(b['50'].map((v, i) => `${x(i)},${y(v)}`).join(' '), C.line, 2.6);
  [b['90'], b['50'], b['10']].forEach(arr =>
    g += T(W - p.r + 6, y(arr[N - 1]) + 4, SGN(arr[N - 1], 1), { a: 'start', c: C.ink, s: 11.5, w: 700 }));
  // 何のリターンかを縦軸に書く。これが無いと数字が何を指すのか分からない。
  g += `<text x="14" y="${p.t + ih / 2}" font-size="11" fill="${C.text}"
    transform="rotate(-90 14 ${p.t + ih / 2})" text-anchor="middle"
    font-family="Zen Kaku Gothic New,sans-serif">ビットコインの累積リターン</text>`;
  svg('fan', g);
  attachHover('fan', {
    vbW: W, x0: p.l, x1: W - p.r, n: N, top: p.t, bottom: p.t + ih,
    labels: Array.from({ length: N }, (_, i) => `${i}営業日後`), yOf: y,
    series: [
      { name: '上位10%', color: C.band10, values: b['90'], fmt: v => SGN(v, 1) },
      { name: '上位25%', color: C.band25, values: b['75'], fmt: v => SGN(v, 1) },
      { name: '中央値', color: C.line, values: b['50'], fmt: v => SGN(v, 1) },
      { name: '下位25%', color: C.band25, values: b['25'], fmt: v => SGN(v, 1) },
      { name: '下位10%', color: C.band10, values: b['10'], fmt: v => SGN(v, 1) },
    ]
  });
  note.innerHTML =
    `<b>読み方。</b>これは予測ではありません。過去に似た条件だったとき、その後20営業日で
     どう散らばったかの実績です。独立した局面は ${a.n_independent} 回。
     中央値が ${b['50'][N - 1] >= 0 ? '上' : '下'}を向いていても、
     下位10%は ${SGN(b['10'][N - 1], 1)}、上位10%は ${SGN(b['90'][N - 1], 1)} です。
     幅の方を見てください。`;
}

/* ---------- 相関マトリクス ---------- */
function drawMatrix(D) {
  const M = D.matrix;
  if (!M || !M.keys.length) { $('mtx').innerHTML = ''; return; }
  let h = '<tr><th></th>' + M.names.map(n => `<th>${n}</th>`).join('') + '</tr>';
  M.values.forEach((row, i) => {
    h += `<tr><th class="row">${M.names[i]}</th>` + row.map(v => {
      if (v === null) return '<td><div class="cell" style="background:var(--surface2);color:var(--muted)">—</div></td>';
      const strong = Math.abs(v) > .6;
      return `<td><div class="cell" style="background:${corrColor(v)};color:${strong ? '#0E161C' : C.ink};padding:9px 4px">${v.toFixed(2)}</div></td>`;
    }).join('') + '</tr>';
  });
  $('mtx').innerHTML = h;
}

/* ---------- 通貨換算 ---------- */
function drawFx(D) {
  const F2 = D.fx_adjusted || [];
  if (!F2.length) { $('fxBox').innerHTML = '<p class="note">データなし</p>'; return; }
  $('fxBox').innerHTML = `<table><tr>
    <th style="text-align:left;font-size:12px;color:var(--muted);padding:6px 20px">資産</th>
    <th style="text-align:right;font-size:12px;color:var(--muted);padding:6px 12px">年初来（円建て）</th>
    <th style="text-align:right;font-size:12px;color:var(--muted);padding:6px 12px">年初来（ドル建て）</th>
    <th style="text-align:right;font-size:12px;color:var(--muted);padding:6px 20px">差</th></tr>`
    + F2.map(r => {
      const gap = (r.ytd_jpy ?? 0) - (r.ytd_usd ?? 0);
      return `<tr><td style="padding:9px 20px">${r.name}</td>
        <td class="px ${CLS(r.ytd_jpy)}" style="padding:9px 12px">${SGN(r.ytd_jpy)}</td>
        <td class="px ${CLS(r.ytd_usd)}" style="padding:9px 12px">${SGN(r.ytd_usd)}</td>
        <td class="px ${CLS(gap)}" style="padding:9px 20px">${SGN(gap)}</td></tr>`;
    }).join('') + '</table>';
}

/* ---------- 暗号資産の温度 ---------- */
function drawCrypto(D) {
  const e = D.extra || {}, hl = e.hyperliquid, fg = e.fear_greed, cg = e.crypto_global;
  const tiles = [];
  if (fg) tiles.push(['恐怖・強欲指数', fg.value, fg.label,
    `1週間前は ${fg.week_ago ?? '—'}、1か月前は ${fg.month_ago ?? '—'}`]);
  if (cg) tiles.push(['BTCドミナンス', F(cg.btc_dominance, 1), '%',
    `暗号資産全体の時価総額 ${F(cg.total_mcap_usd / 1e12, 2)}兆ドル`]);
  if (hl && hl.BTC) {
    tiles.push(['BTC資金調達レート', SGN(hl.BTC.funding_hourly_pct, 4, ''), '% / 1時間',
      `年率換算 ${SGN(hl.BTC.funding_annual_pct, 1)}。Hyperliquidは1時間ごとに精算`]);
    tiles.push(['BTC建玉', F(hl.BTC.open_interest_usd / 1e8, 2), '億ドル',
      'Hyperliquid のみ']);
  }
  $('cryptoTiles').innerHTML = tiles.length ? tiles.map(([k, v, u, n]) =>
    `<div class="tile"><p class="k">${k}</p><p class="v">${v}<span class="u">${u}</span></p>
     <p class="n">${n}</p></div>`).join('')
    : '<p class="note">暗号資産の周辺データを取得できませんでした。</p>';
}

/* ---------- 市場一覧 ---------- */
const CATS = ['米国株', 'アジア株', '欧州株', '先物', '商品', '為替', '金利', '暗号資産'];
let SUMMARY = {};
function drawTables(D) {
  const S = D.summary || [];
  const live = D.live || {};
  S.forEach(r => SUMMARY[r.key] = r);
  $('tables').innerHTML = CATS.map(cat => {
    const rows = S.filter(r => r.cat === cat);
    if (!rows.length) return '';
    return `<div class="panel"><h3>${cat}</h3><table><tbody>` + rows.map(r => {
      const suf = r.unit === 'rate' ? 'pt' : '%';
      const lv = live[r.key];
      const sub = (lv && lv.live && Math.abs(lv.live - r.last) / (r.last || 1) > 0.001)
        ? `確定 ${r.last_date} ／ 現在 ${F(lv.live, r.last > 1000 ? 0 : 3)}`
        : `確定 ${r.last_date}`;
      return `<tr class="clickable" data-key="${r.key}" tabindex="0" role="button">
        <td class="nm">${r.name}<small>${sub}</small></td>
        <td class="px">${F(r.last, r.last > 1000 ? 0 : (r.unit === 'rate' ? 3 : 2))}</td>
        <td class="ch ${CLS(r.chg1d)}">${SGN(r.chg1d, 2, suf)}</td>
        <td style="width:74px;padding-right:16px">${spark(r.spark)}</td></tr>`;
    }).join('') + '</tbody></table></div>';
  }).join('');
  $('tables').querySelectorAll('tr.clickable').forEach(tr => {
    const open = () => openChart(tr.dataset.key);
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
  });
}
function spark(a) {
  if (!a || a.length < 3) return '';
  const mn = Math.min(...a), mx = Math.max(...a), r = (mx - mn) || 1;
  const pts = a.map((v, i) => `${(i / (a.length - 1) * 62).toFixed(1)},${(20 - (v - mn) / r * 20).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 62 20" width="62" height="20" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="${a[a.length - 1] >= a[0] ? C.up : C.down}"
    stroke-width="1.6" stroke-linejoin="round" opacity=".85"/></svg>`;
}

/* ---------- 拡大表示（ローソク足） ----------
   四本値は latest.json ではなく history.json に入れている。
   毎回読むには重いので、最初にクリックされたときだけ取りに行って覚えておく。 */
let HIST = null, HIST_ERR = null, MODAL_KEY = null, MODAL_RANGE = 120;
async function loadHistory() {
  if (HIST || HIST_ERR) return HIST;
  try {
    const r = await fetch('data/history.json?t=' + Date.now());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    HIST = await r.json();
  } catch (e) { HIST_ERR = e.message; }
  return HIST;
}
async function openChart(key) {
  MODAL_KEY = key; MODAL_RANGE = 120;
  const m = $('modal'), row = SUMMARY[key] || {};
  m.hidden = false; document.body.style.overflow = 'hidden';
  $('modalTitle').textContent = row.name || key;
  $('modalSub').textContent = '読み込み中…';
  $('modalBody').innerHTML = '<div class="loading">四本値を読み込んでいます…</div>';
  const url = row.code
    ? 'https://finance.yahoo.com/quote/' + encodeURIComponent(row.code)
    : null;
  $('modalLink').innerHTML = url
    ? `<a href="${url}" target="_blank" rel="noopener">Yahoo Finance で見る（${row.code}）↗</a>`
    : '';
  await loadHistory();
  renderModal();
}
function closeChart() {
  $('modal').hidden = true; document.body.style.overflow = '';
  if (TIPHOST) TIPHOST.remove(), TIPHOST = null;
}
let TIPHOST = null;
function renderModal() {
  const key = MODAL_KEY, row = SUMMARY[key] || {};
  if (!HIST || !HIST.ohlc || !HIST.ohlc[key]) {
    $('modalBody').innerHTML = `<p class="note">この銘柄の四本値がありません。
      ${HIST_ERR ? '履歴ファイルを読めませんでした（' + HIST_ERR + '）。' :
        '取得元が四本値を返していない可能性があります。'}</p>`;
    $('modalSub').textContent = '';
    return;
  }
  const bars = HIST.ohlc[key];
  const dates = Object.keys(bars).sort();
  const use = dates.slice(-MODAL_RANGE);
  const o = use.map(d => bars[d]);
  const hi = Math.max(...o.map(b => b[1])), lo = Math.min(...o.map(b => b[2]));
  const pad = (hi - lo) * 0.06 || 1;
  const HI = hi + pad, LO = lo - pad;
  const W = 900, H = 380, p = { t: 16, r: 66, b: 30, l: 14 };
  const iw = W - p.l - p.r, ih = H - p.t - p.b;
  const bw = iw / use.length;
  const x = i => p.l + bw * (i + 0.5), y = v => p.t + ((HI - v) / (HI - LO)) * ih;
  const dg = row.unit === 'rate' ? 3 : (HI > 1000 ? 0 : HI > 10 ? 2 : 4);

  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = LO + (HI - LO) * i / 4;
    g += `<line x1="${p.l}" y1="${y(v)}" x2="${W - p.r}" y2="${y(v)}" stroke="${C.grid}"/>`
      + T(W - p.r + 6, y(v) + 4, F(v, dg), { a: 'start' });
  }
  o.forEach((b, i) => {
    const up = b[3] >= b[0], col = up ? C.up : C.down;
    g += `<line x1="${x(i)}" y1="${y(b[1])}" x2="${x(i)}" y2="${y(b[2])}"
      stroke="${col}" stroke-width="1"/>`;
    const top = Math.min(y(b[0]), y(b[3])), h = Math.max(Math.abs(y(b[0]) - y(b[3])), 1);
    g += `<rect x="${x(i) - bw * 0.34}" y="${top}" width="${Math.max(bw * 0.68, 1)}"
      height="${h}" fill="${col}" opacity=".9"/>`;
  });
  // 端のラベルは中央揃えだと枠の外に出るので、左端は左寄せ・右端は右寄せにする
  g += T(p.l, H - 10, use[0], { s: 10, a: 'start' })
     + T((p.l + W - p.r) / 2, H - 10, use[Math.floor(use.length / 2)], { s: 10 })
     + T(W - p.r, H - 10, use[use.length - 1], { s: 10, a: 'end' });

  const first = o[0][3], last = o[o.length - 1][3];
  const chg = (last / first - 1) * 100;
  $('modalSub').innerHTML =
    `${use[0]} 〜 ${use[use.length - 1]}（${use.length}本）　
     期間騰落 <b class="${CLS(chg)}">${SGN(chg)}</b>　
     高値 ${F(hi, dg)}　安値 ${F(lo, dg)}`;
  $('modalBody').innerHTML =
    `<div class="ranges">${[[60, '3か月'], [120, '6か月'], [250, '1年'], [9999, '全期間']]
      .map(([n, l]) => `<button data-n="${n}" class="${n === MODAL_RANGE ? 'on' : ''}">${l}</button>`).join('')}</div>
     <div class="chartbox"><svg id="candle" viewBox="0 0 ${W} ${H}"></svg></div>`;
  svg('candle', g);
  attachHover('candle', {
    vbW: W, x0: x(0), x1: x(use.length - 1), n: use.length, top: p.t, bottom: p.t + ih,
    labels: use, yOf: y,
    series: [
      { name: '始値', color: C.text, values: o.map(b => b[0]), fmt: v => F(v, dg) },
      { name: '高値', color: C.up, values: o.map(b => b[1]), fmt: v => F(v, dg) },
      { name: '安値', color: C.down, values: o.map(b => b[2]), fmt: v => F(v, dg) },
      { name: '終値', color: C.aqua, values: o.map(b => b[3]), fmt: v => F(v, dg) },
    ],
  });
  $('modalBody').querySelectorAll('.ranges button').forEach(b =>
    b.addEventListener('click', () => { MODAL_RANGE = +b.dataset.n; renderModal(); }));
}

/* ---------- 取得ログ ---------- */
function drawLog(D) {
  const h = D.health || {};
  const src = Object.entries(h.sources || {}).map(([k, v]) => `${k}: ${v ? '正常' : '取得失敗'}`).join(' / ');
  $('logBox').innerHTML =
    `<p class="note">周辺データ：${src || '—'}</p>`
    + `<details><summary>取得ログを見る（${(h.log || []).length}行）</summary>
       <pre>${(h.log || []).join('\n').replace(/</g, '&lt;')}</pre></details>`;
}

/* ダイアログの開閉 */
$('modalClose').addEventListener('click', closeChart);
$('modal').addEventListener('click', e => { if (e.target.id === 'modal') closeChart(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('modal').hidden) closeChart(); });

main();
