const $ = (selector) => document.querySelector(selector);
const fmt = (x, digits = 2) => x == null ? '—' : Number(x).toLocaleString('zh-CN', { maximumFractionDigits: digits });
const pct = (x) => x == null ? '—' : `${(100 * x).toFixed(1)}%`;
const yuan = (x) => x == null ? '—' : `¥${fmt(x)}`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function metric(label, value, note) {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
}

function renderIntraday(rows) {
  const el = $('#intradayChart');
  const w = 720, h = 250, left = 44, right = 8, top = 12, bottom = 27;
  const innerW = w - left - right, innerH = h - top - bottom;
  const max = Math.max(...rows.map((x) => x.turnover));
  const step = innerW / rows.length, barW = step * .68;
  let svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="5分钟成交额柱状图">`;
  for (let i = 0; i <= 4; i++) {
    const y = top + innerH * i / 4;
    svg += `<line class="gridline" x1="${left}" x2="${w-right}" y1="${y}" y2="${y}"/>`;
    svg += `<text class="axis" x="${left-5}" y="${y+3}" text-anchor="end">${fmt(max*(1-i/4)/1e8,0)}亿</text>`;
  }
  rows.forEach((row, i) => {
    const barH = innerH * row.turnover / max, y = top + innerH - barH;
    const x = left + i * step + (step - barW) / 2;
    svg += `<rect class="bar" x="${x}" y="${y}" width="${barW}" height="${barH}" rx="2"><title>${row.time} · ${yuan(row.turnover)} · ${fmt(row.trade_count,0)} 笔</title></rect>`;
    if (i % Math.ceil(rows.length / 8) === 0) svg += `<text class="axis" x="${x+barW/2}" y="${h-7}" text-anchor="middle">${row.time}</text>`;
  });
  el.innerHTML = `${svg}</svg>`;
}

function renderBands(rows) {
  const maxTrade = Math.max(...rows.map((x) => x.trade_share));
  const maxTurnover = Math.max(...rows.map((x) => x.turnover_share));
  $('#sizeChart').innerHTML = rows.map((row) => `<div class="band">
    <span class="band-name">${esc(row.amount_band)}</span>
    <div class="bars">
      <div class="track" title="成交笔数占比"><div class="fill" style="width:${100*row.trade_share/maxTrade}%"></div></div>
      <div class="track" title="成交额占比"><div class="fill turnover" style="width:${100*row.turnover_share/maxTurnover}%"></div></div>
    </div><span class="band-values">${pct(row.trade_share)} 笔<br>${pct(row.turnover_share)} 额</span>
  </div>`).join('') + `<div class="legend"><span><i></i>成交笔数占比</span><span><i class="gold"></i>成交额占比</span></div>`;
}

function renderFlow(scope) {
  const buy = scope.active_buy_turnover, sell = scope.active_sell_turnover;
  const max = Math.max(buy, sell);
  $('#flowBars').innerHTML = `<div class="flow-row"><span>主动买入</span><div class="track"><div class="fill" style="width:${100*buy/max}%"></div></div><b>${yuan(buy)}</b></div>
    <div class="flow-row"><span>主动卖出</span><div class="track"><div class="fill sell" style="width:${100*sell/max}%"></div></div><b>${yuan(sell)}</b></div>`;
  $('#flowNote').textContent = `主动买卖额差（买入减卖出 / 两者合计）：${pct(scope.active_flow_imbalance)}。该指标描述成交方向压力，不代表资金净流入。`;
}

function renderOrders(scope) {
  $('#orderSummary').innerHTML = `<div class="order-stat"><span>新增委托</span><b>${fmt(scope.order_add_count,0)} 笔</b><small>${fmt(scope.order_add_volume,0)} 股</small></div>
    <div class="order-stat"><span>撤单</span><b>${fmt(scope.order_cancel_count,0)} 笔</b><small>${fmt(scope.order_cancel_volume,0)} 股</small></div>
    <div class="order-ratio"><span>撤单量 / 新增量</span><strong>${pct(scope.observed_cancel_add_volume_ratio)}</strong></div>`;
}

function stockByCode(code) {
  return reportData.stocks.find((stock) => Number(stock.Symbol) === Number(code));
}

function renderStock(code) {
  const stock = stockByCode(code);
  if (!stock) return;
  const largeTurnoverShare = stock.large_trade_turnover_share;
  $('#stockMetrics').innerHTML = [
    metric('连续竞价成交额', yuan(stock.turnover), `${fmt(stock.trade_count,0)} 笔 · ${fmt(stock.trade_volume,0)} 股`),
    metric('单笔成交额中位数 / P90', `${yuan(stock.median_trade_amount)} / ${yuan(stock.p90_trade_amount)}`, '逐笔成交分布'),
    metric('≥10 万元成交占比', pct(largeTurnoverShare), `${fmt(stock.large_trade_count,0)} 笔 · 笔数占 ${pct(stock.large_trade_count_share)}`),
    metric('主动买卖额差', pct(stock.trade_flow_imbalance), `主动买入 ${yuan(stock.active_buy_turnover)} · 主动卖出 ${yuan(stock.active_sell_turnover)}`),
    metric('撤单 / 新增委托量', pct(stock.cancel_add_volume_ratio), `${fmt(stock.cancel_count,0)} 撤单 / ${fmt(stock.add_count,0)} 新增`),
    metric('中位价差 / L5 深度', `${fmt(stock.median_spread_bps,2)} bp`, yuan(stock.median_l5_depth_value)),
    metric('L5 买卖深度失衡', pct(stock.median_l5_depth_imbalance), '正值表示五档买盘金额较大'),
    metric('新增 / 撤单方向失衡', `${pct(stock.add_volume_imbalance)} / ${pct(stock.cancel_volume_imbalance)}`, '买方量为正、卖方量为负'),
  ].join('');
  const series = reportData.stock_intraday_5m.filter((row) => Number(row.Symbol) === Number(code));
  renderStockIntraday(series);
}

function renderStockIntraday(rows) {
  const el = $('#stockIntraday');
  if (!rows.length) { el.textContent = '所选证券没有连续竞价成交。'; return; }
  const w = 900, h = 205, left = 45, right = 8, top = 10, bottom = 27;
  const innerW = w-left-right, innerH = h-top-bottom, max = Math.max(...rows.map((x) => x.turnover));
  const step = innerW / rows.length, barW = step*.68;
  let svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="所选证券5分钟成交额">`;
  for (let i=0; i<=3; i++) {
    const y=top+innerH*i/3;
    svg += `<line class="gridline" x1="${left}" x2="${w-right}" y1="${y}" y2="${y}"/><text class="axis" x="${left-5}" y="${y+3}" text-anchor="end">${fmt(max*(1-i/3)/1e6,0)}百万</text>`;
  }
  rows.forEach((row,i)=>{
    const barH=innerH*row.turnover/max, x=left+i*step+(step-barW)/2, y=top+innerH-barH;
    svg += `<rect class="bar" x="${x}" y="${y}" width="${barW}" height="${barH}" rx="2"><title>${row.time} · 成交额 ${yuan(row.turnover)} · 笔数 ${fmt(row.trade_count,0)} · 主动流差 ${pct(row.signed_turnover/row.turnover)} · 大额占比 ${pct(row.large_turnover/row.turnover)}</title></rect>`;
    if(i%Math.ceil(rows.length/9)===0) svg += `<text class="axis" x="${x+barW/2}" y="${h-7}" text-anchor="middle">${row.time}</text>`;
  });
  el.innerHTML=`${svg}</svg>`;
}

function renderStockTable() {
  const filter = $('#stockFilter').value.trim();
  const rows = reportData.stocks.filter((stock) => String(stock.Symbol).includes(filter)).sort((a,b)=>Number(a.Symbol)-Number(b.Symbol));
  $('#stockTable').innerHTML = `<thead><tr><th>证券</th><th>成交额</th><th>成交笔数</th><th>单笔中位额</th><th>≥10万元成交额占比</th><th>主动流差</th><th>撤单/新增量</th><th>中位价差</th><th>L5 深度</th><th>L5 失衡</th></tr></thead><tbody>${rows.map((stock)=>`<tr class="stock-row ${Number(stock.Symbol)===Number($('#stockSelect').value)?'selected':''}" data-symbol="${stock.Symbol}"><td>${String(stock.Symbol).padStart(6,'0')}</td><td>${yuan(stock.turnover)}</td><td>${fmt(stock.trade_count,0)}</td><td>${yuan(stock.median_trade_amount)}</td><td>${pct(stock.large_trade_turnover_share)}</td><td>${pct(stock.trade_flow_imbalance)}</td><td>${pct(stock.cancel_add_volume_ratio)}</td><td>${fmt(stock.median_spread_bps,2)} bp</td><td>${yuan(stock.median_l5_depth_value)}</td><td>${pct(stock.median_l5_depth_imbalance)}</td></tr>`).join('')}</tbody>`;
  document.querySelectorAll('.stock-row').forEach((row)=>row.addEventListener('click',()=>selectStock(row.dataset.symbol)));
}

function selectStock(code) {
  $('#stockSelect').value = String(code);
  renderStock(code);
  renderStockTable();
  loadRawRows(0);
}

function timeLabel(raw) {
  const n=Number(raw), h=Math.floor(n/10_000_000), m=Math.floor(n/100_000)%100, s=Math.floor(n/1_000)%100, ms=n%1_000;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}.${String(ms).padStart(3,'0')}`;
}

function renderRawRows(data) {
  const columns=data.columns;
  $('#rawTable').innerHTML=`<thead><tr>${columns.map((col)=>`<th>${esc(col)}</th>`).join('')}</tr></thead><tbody>${data.rows.map((row)=>`<tr>${columns.map((col)=>`<td>${row[col]==null?'':esc(col==='Time'?timeLabel(row[col]):String(row[col]))}</td>`).join('')}</tr>`).join('')}</tbody>`;
  const from=data.total?data.offset+1:0, to=Math.min(data.total,data.offset+data.rows.length);
  $('#rawInfo').textContent=`${data.dataset.toUpperCase()} · ${String(data.symbol).padStart(6,'0')} · 共 ${fmt(data.total,0)} 行`;
  $('#rawPage').textContent=`${fmt(from,0)}–${fmt(to,0)} / ${fmt(data.total,0)}`;
  $('#rawPrev').disabled=data.offset===0;
  $('#rawNext').disabled=data.offset+data.rows.length>=data.total;
}

async function loadRawRows(offset=0) {
  rawOffset=offset;
  const params=new URLSearchParams({dataset:$('#rawDataset').value,symbol:$('#stockSelect').value,offset:String(offset),limit:String(rawLimit),start:$('#rawStart').value,end:$('#rawEnd').value});
  $('#rawInfo').textContent='正在读取数据…';
  try {
    const response=await fetch(`/api/l2-rows?${params}`), data=await response.json();
    if(!response.ok) throw Error(data.error||'读取失败');
    renderRawRows(data);
  } catch(error) { $('#rawInfo').textContent=error.message; }
}

function renderStrategy(screen) {
  $('#strategyTable').innerHTML=`<table><thead><tr><th>特征</th><th>可以检验的含义</th><th>有效时段</th><th>平均 Rank IC</th><th>IC 为正时段占比</th><th>高组−低组收益差</th><th>收益差为正占比</th><th>下一步</th></tr></thead><tbody>${screen.features.map((item)=>{
    const spread=Number(item.mean_top_minus_bottom_bps), share=Number(item.positive_spread_period_share), stable=share>=.55||share<=.45;
    const cue=stable?(spread>0?'高值侧可形成假设':'低值侧可形成假设'):'方向暂不稳定';
    const direction=spread>=0?'high':'low';
    return `<tr><td>${esc(item.label)}</td><td class="strategy-hypothesis">${esc(item.description)} <b>${cue}</b></td><td>${fmt(item.valid_periods,0)}</td><td>${fmt(item.mean_rank_ic,3)}</td><td>${pct(item.positive_ic_period_share)}</td><td class="${spread>=0?'positive':'negative'}">${fmt(item.mean_top_minus_bottom_bps,2)} bp</td><td>${pct(item.positive_spread_period_share)}</td><td><button type="button" class="idea-seed" data-feature="${esc(item.feature)}" data-direction="${direction}" data-spread="${spread}" data-positive="${item.positive_spread_period_share}">生成假设</button></td></tr>`;
  }).join('')}</tbody></table>`;
  document.querySelectorAll('.idea-seed').forEach((button)=>button.addEventListener('click',()=>{
    const feature=screen.features.find((item)=>item.feature===button.dataset.feature);
    $('#ideaFeature').value=button.dataset.feature;
    $('#ideaDirection').value=button.dataset.direction;
    $('#ideaHorizon').value='5';
    $('#ideaText').value=`${feature.label}处于${button.dataset.direction==='high'?'高':'低'}值时，后续5分钟收益是否高于另一组？当日高低组差为${fmt(button.dataset.spread,2)} bp，正向时段占比为${pct(Number(button.dataset.positive))}。`;
    $('#ideaForm').scrollIntoView({behavior:'smooth',block:'center'});
  }));
  $('#strategyLimits').textContent=screen.limits;
}

function renderIdeaResult(result) {
  const single=result.scope_mode==='single_stock_time_series';
  const edgeLabel=single?'该股高低信号组毛收益差':'单日高低特征组毛收益差';
  const netLabel=single?'假设成本后的信号组均值空间':'假设成本后的组间均值空间';
  const hitLabel=single?'信号样本超过成本情景的占比':'时段收益差超过成本情景的占比';
  const hitValue=single?result.selected_net_positive_share:result.edge_exceeds_cost_share;
  const breakEvenNote=single?`单日信号组毛收益均值 ${fmt(result.break_even_total_cost_bps,2)} bp（成本参考）`:`单日组间均值差 ${fmt(result.break_even_total_cost_bps,2)} bp（成本参考）`;
  $('#ideaStatus').innerHTML=`<div class="idea-result-head"><div><span>评估结果 · ${esc(result.feature_label)} · 后续 ${result.horizon_minutes} 分钟</span><strong>${esc(result.assessment)}</strong></div><small>${esc(result.method)} · ${fmt(result.sample_count,0)} 个有效样本 · 平均 Rank IC ${fmt(result.mean_rank_ic,3)}</small></div>
    <div class="idea-result-metrics">
      <div><span>${edgeLabel}</span><b>${fmt(result.mean_selected_minus_opposite_bps,2)} bp</b><small>${breakEvenNote}</small></div>
      <div><span>${netLabel}</span><b class="${result.net_edge_after_cost_bps>=0?'positive':'negative'}">${fmt(result.net_edge_after_cost_bps,2)} bp</b><small>成本情景 ${fmt(result.assumed_total_cost_bps,2)} bp；非实际盈亏</small></div>
      <div><span>${hitLabel}</span><b>${pct(hitValue)}</b><small>只描述当日样本分布</small></div>
      <div><span>${single?'该股信号触发组毛收益':'所选方向组毛收益'}</span><b>${fmt(result.mean_selected_return_bps,2)} bp</b><small>扣成本情景后 ${fmt(result.selected_net_return_bps,2)} bp</small></div>
    </div><p class="idea-caveat">${esc(result.caveat)}</p>`;
}

function renderIdeaHistory() {
  const history=JSON.parse(localStorage.getItem('l2IdeaHistoryV2')||'[]');
  $('#ideaHistory').innerHTML=history.map((item,index)=>`<button type="button" class="idea-history-item" data-index="${index}"><span>${esc(item.idea)}</span><b>${esc(item.assessment)} · 情景空间 ${fmt(item.net_edge_after_cost_bps,2)} bp</b></button>`).join('');
  document.querySelectorAll('.idea-history-item').forEach((button)=>button.addEventListener('click',()=>renderIdeaResult(history[Number(button.dataset.index)])));
}

async function submitIdea(event) {
  event.preventDefault();
  const button=$('#ideaSubmit');
  button.disabled=true;
  $('#ideaStatus').textContent='正在按所选特征和历史样本评估…';
  const symbolScope=$('#ideaScope').value;
  const payload={
    idea:$('#ideaText').value,
    feature:$('#ideaFeature').value,
    direction:$('#ideaDirection').value,
    horizon:Number($('#ideaHorizon').value),
    quantile:Number($('#ideaQuantile').value),
    cost_bps:Number($('#ideaCost').value),
    symbols:symbolScope==='selected'?[Number($('#stockSelect').value)]:[],
  };
  try {
    const response=await fetch('/api/strategy-evaluate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const result=await response.json();
    if(!response.ok) throw Error(result.error||'评估失败');
    renderIdeaResult(result);
    const history=JSON.parse(localStorage.getItem('l2IdeaHistoryV2')||'[]');
    history.unshift(result);
    localStorage.setItem('l2IdeaHistoryV2',JSON.stringify(history.slice(0,10)));
    renderIdeaHistory();
  } catch(error) {
    $('#ideaStatus').textContent=`无法评估：${error.message}`;
  } finally { button.disabled=false; }
}

function render(data) {
  reportData = data;
  const scope = data.scope, large = data.trade_size.ge_100k;
  $('#loading').hidden = true;
  $('#dashboard').hidden = false;
  $('#metrics').innerHTML = [
    metric('连续竞价成交额', yuan(scope.regular_trade_turnover), `${fmt(scope.directional_trade_count,0)} 笔带方向成交`),
    metric('≥10 万元单笔', pct(large.trade_share), `笔数 ${fmt(large.trade_count,0)} 笔 · 成交额占 ${pct(large.turnover_share)}`),
    metric('主动买入 / 卖出额差', pct(scope.active_flow_imbalance), '按成交方向分类的订单流压力'),
    metric('撤单 / 新增委托量比', pct(scope.observed_cancel_add_volume_ratio), '逐笔委托消息观察比率'),
    metric('集合竞价成交', `${fmt(scope.auction_trade_count,0)} 笔`, yuan(scope.auction_trade_turnover)),
    metric('L5 中位价差', `${fmt(data.snapshots.median_spread_bps,2)} bp`, '连续竞价快照统计'),
  ].join('');
  $('#rawInventory').textContent = `数据覆盖：ord 委托 ${fmt(scope.orders_rows,0)} 行 · exe 成交 ${fmt(scope.trade_rows_all,0)} 行 · snp 快照 ${fmt(scope.snapshot_rows,0)} 行。明细表支持单证券、时段筛选和分页；下载按钮提供原始完整文件。`;
  renderIntraday(data.intraday_5m);
  renderBands(data.trade_size.bands);
  renderFlow(scope);
  renderOrders(scope);
  $('#stockSelect').innerHTML=data.stocks.slice().sort((a,b)=>Number(a.Symbol)-Number(b.Symbol)).map((stock)=>`<option value="${stock.Symbol}">${String(stock.Symbol).padStart(6,'0')}</option>`).join('');
  $('#stockSelect').addEventListener('change',()=>selectStock($('#stockSelect').value));
  $('#stockFilter').addEventListener('input',renderStockTable);
  $('#rawLoad').addEventListener('click',()=>loadRawRows(0));
  $('#rawDataset').addEventListener('change',()=>loadRawRows(0));
  $('#rawPrev').addEventListener('click',()=>loadRawRows(Math.max(0,rawOffset-rawLimit)));
  $('#rawNext').addEventListener('click',()=>loadRawRows(rawOffset+rawLimit));
  renderStock(data.stocks[0].Symbol);
  renderStockTable();
  loadRawRows(0);
  renderStrategy(data.strategy_screen);
  $('#ideaFeature').innerHTML=data.strategy_screen.features.map((item)=>`<option value="${esc(item.feature)}">${esc(item.label)}</option>`).join('');
  $('#ideaForm').addEventListener('submit',submitIdea);
  renderIdeaHistory();
  $('#rules').innerHTML = `<div class="rule"><b>ord</b><span>委托事件流。FunctionCode 66/83 对应 B/S 方向；OrderKind 65/68 对应新增/撤单。保留其它状态/标记代码，别一概当作普通委托。</span></div>
    <div class="rule"><b>exe</b><span>成交事件流，包含买卖委托引用、成交价量和 BSFlag。主动方向额差是交易压力代理，不代表资金净流入。</span></div>
    <div class="rule"><b>snp</b><span>行情快照含买卖 10 档。变动触发快照与周期快照交替出现，因此快照数量不等于逐笔消息数量。</span></div>
    <div class="rule"><b>Channel</b><span>BizIndex 在 Channel 内排序；这些文件只抽取 50 只证券，样本序号出现间隔不能直接判作丢包。</span></div>
    <div class="rule"><b>大/小单</b><span>按单笔成交价 × 股数分层。这里以 10 万元作一个观察阈值；单笔阈值不识别投资者身份。</span></div>`;
  $('#snapshotMetrics').innerHTML = [
    ['连续竞价快照', `${fmt(data.snapshots.continuous_rows_used,0)} 行`],
    ['单证券快照间隔中位数', `${fmt(data.snapshots.median_update_gap_ms,0)} ms`],
    ['L5 深度中位数', yuan(data.snapshots.median_depth_value5)],
    ['L5 买卖深度失衡中位数', pct(data.snapshots.median_depth_imbalance5)],
  ].map(([label, value]) => `<div class="snapshot-item"><span>${label}</span><b>${value}</b></div>`).join('');
  $('#limits').innerHTML = data.interpretation_limits.map((item) => `<li>${esc(item)}</li>`).join('');
}

let reportData = null;
let rawOffset = 0;
const rawLimit = 100;

fetch('/api/research')
  .then((response) => { if (!response.ok) throw Error('报告未生成'); return response.json(); })
  .then(render)
  .catch((error) => { $('#loading').textContent = `无法读取分析结果：${error.message}。请先运行 python generate_l2_research.py。`; });
