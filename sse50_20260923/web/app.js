(() => {
  const $ = (selector) => document.querySelector(selector);
  const els = {
    clock: $('#clock'), session: $('#sessionBadge'), timeline: $('#timeline'), timelinePercent: $('#timelinePercent'),
    symbol: $('#symbolSelect'), last: $('#lastPrice'), bestBid: $('#bestBid'), bestAsk: $('#bestAsk'),
    bookRows: $('#bookRows'), bookTime: $('#bookTime'), tradeRows: $('#tradeRows'), tradeCount: $('#tradeCount'),
    availableCash: $('#availableCash'), equity: $('#equity'), marketValue: $('#marketValue'), unrealized: $('#unrealizedPnl'),
    cash: $('#cash'), reservedCash: $('#reservedCash'), positionQuantity: $('#positionQuantity'), availablePosition: $('#availablePosition'),
    averageCost: $('#averageCost'), realizedPnl: $('#realizedPnl'), orderPrice: $('#orderPrice'), orderQuantity: $('#orderQuantity'),
    orderCapacity: $('#orderCapacity'), submitOrder: $('#submitOrder'), orderRows: $('#orderRows'), openOrderCount: $('#openOrderCount'),
    fillRows: $('#fillRows'), fillCount: $('#fillCount'), positionRows: $('#positionRows'), playButton: $('#playButton'),
    playIcon: $('#playIcon'), playText: $('#playText'), toast: $('#toast'), jumpTime: $('#jumpTime'),
  };

  let meta = null;
  let state = null;
  let selectedSide = 'BUY';
  let playing = false;
  let busy = false;
  let playTimer = null;
  let toastTimer = null;
  let seekDebounce = null;
  let priceEdited = false;
  let candleData = [];
  let candleInterval = 1;
  let chartViewport = [];
  let chartHoverIndex = -1;

  const fmtMoney = (value) => `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const fmtPrice = (value) => value == null || !Number.isFinite(Number(value)) || Number(value) <= 0 ? '—' : Number(value).toFixed(2);
  const fmtQty = (value) => Number(value || 0).toLocaleString('zh-CN');
  const rawText = (raw) => {
    const value = Number(raw || 0);
    const h = Math.floor(value / 10000000);
    const m = Math.floor(value / 100000) % 100;
    const s = Math.floor(value / 1000) % 100;
    const ms = value % 1000;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
  };
  const timeOfDay = (raw) => rawText(raw).slice(0, 8);
  const esc = (value) => String(value ?? '');

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
    return payload;
  }

  function notify(message, isError = false) {
    els.toast.textContent = message;
    els.toast.classList.toggle('error', isError);
    els.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.remove('show'), 2600);
  }

  function setBusy(value) {
    busy = value;
    els.playButton.disabled = value;
    els.symbol.disabled = value;
  }

  function sessionName(raw, session = '') {
    if (session === 'opening_auction' || raw < 93000000) return '集合竞价';
    if (session === 'closing_auction') return '收盘集合竞价';
    if (session === 'after_hours_fixed_price') return '盘后定价';
    if (raw >= 130000000) return '连续竞价 · 午后';
    if (raw >= 113000000) return '午间休市';
    if (raw >= 93000000) return '连续竞价 · 上午';
    return '盘前';
  }

  function setText(selector, value) {
    $(selector).textContent = value;
  }

  function cell(text, className = '') {
    const td = document.createElement('td');
    td.textContent = esc(text);
    if (className) td.className = className;
    return td;
  }

  function emptyRow(colspan, message) {
    const tr = document.createElement('tr');
    const td = cell(message, 'empty-row');
    td.colSpan = colspan;
    tr.append(td);
    return tr;
  }

  function sideLabel(side) {
    if (side === 'BUY') return ['买入', 'buy'];
    if (side === 'SELL') return ['卖出', 'sell'];
    return ['集合', ''];
  }

  function renderBook(market) {
    const bids = market.bids || [];
    const asks = market.asks || [];
    const maxVol = Math.max(1, ...bids.map((x) => x.volume), ...asks.map((x) => x.volume));
    els.bookRows.replaceChildren();
    for (let i = 0; i < 5; i += 1) {
      const bid = bids[i];
      const ask = asks[i];
      const tr = document.createElement('tr');
      tr.append(cell(`L${i + 1}`));
      const bidVol = document.createElement('td');
      bidVol.className = 'align-right depth-cell';
      if (bid) {
        const bar = document.createElement('i');
        bar.className = 'depth-bar bid';
        bar.style.width = `${Math.max(2, 72 * bid.volume / maxVol)}%`;
        const label = document.createElement('span');
        label.textContent = fmtQty(bid.volume);
        bidVol.append(bar, label);
      } else bidVol.textContent = '—';
      tr.append(bidVol, cell(bid ? fmtPrice(bid.price) : '—', 'align-right book-bid'));
      tr.append(cell(ask ? fmtPrice(ask.price) : '—', 'align-right book-ask'));
      const askVol = document.createElement('td');
      askVol.className = 'align-right depth-cell';
      if (ask) {
        const bar = document.createElement('i');
        bar.className = 'depth-bar ask';
        bar.style.width = `${Math.max(2, 72 * ask.volume / maxVol)}%`;
        const label = document.createElement('span');
        label.textContent = fmtQty(ask.volume);
        askVol.append(bar, label);
      } else askVol.textContent = '—';
      tr.append(askVol);
      els.bookRows.append(tr);
    }
    els.bookTime.textContent = `盘口时间 ${market.Time ? rawText(market.Time) : '—'}`;
    setText('#bestBid', bids[0] ? fmtPrice(bids[0].price) : '—');
    setText('#bestAsk', asks[0] ? fmtPrice(asks[0].price) : '—');
  }

  function renderTrades(market) {
    const trades = market.recent_trades || [];
    els.tradeRows.replaceChildren();
    els.tradeCount.textContent = `${trades.length} 笔`;
    if (!trades.length) {
      els.tradeRows.append(emptyRow(4, '当前时刻暂无逐笔成交'));
      return;
    }
    for (const trade of trades) {
      const tr = document.createElement('tr');
      const [label, sideClass] = sideLabel(trade.aggressor);
      tr.append(cell(trade.time_text.slice(0, 12)));
      const sideCell = document.createElement('td');
      const side = document.createElement('span');
      side.className = `trade-side ${sideClass}`.trim();
      side.textContent = label;
      sideCell.append(side);
      tr.append(sideCell, cell(fmtPrice(trade.price), `align-right ${sideClass === 'buy' ? 'book-bid' : sideClass === 'sell' ? 'book-ask' : ''}`), cell(fmtQty(trade.volume), 'align-right'));
      els.tradeRows.append(tr);
    }
  }

  function rawSeconds(raw) {
    const value = Number(raw || 0);
    return Math.floor(value / 10000000) * 3600 + Math.floor(value / 100000) % 100 * 60 + Math.floor(value / 1000) % 100;
  }

  function clockSeconds(value) {
    const match = String(value || '').match(/^(\d{2}):(\d{2}):(\d{2})/);
    return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) : null;
  }

  function clockFromSeconds(value) {
    const hour = Math.floor(value / 3600);
    const minute = Math.floor(value % 3600 / 60);
    const second = value % 60;
    return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`;
  }

  function drawChart() {
    const canvas = $('#klineCanvas');
    const stage = $('#chartStage');
    if (!canvas || !stage) return;
    const rect = stage.getBoundingClientRect();
    const width = Math.max(300, rect.width);
    const height = Math.max(190, rect.height);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const left = 54;
    const right = 13;
    const top = 12;
    const bottom = height - 54;
    const volumeTop = height - 46;
    const volumeHeight = 25;
    const plotWidth = width - left - right;
    const plotHeight = bottom - top;
    const nowSeconds = state ? rawSeconds(state.time) : 0;
    const finished = candleData.filter((bar) => bar.end_seconds <= nowSeconds);
    const maxBars = width < 540 ? 62 : 100;
    chartViewport = finished.slice(-maxBars);
    $('#chartBarCount').textContent = `${chartViewport.length} 根已完成 K 线`;
    $('#chartSession').textContent = state ? sessionName(state.time, state.market.Session) : '—';

    ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'middle';
    if (!chartViewport.length) {
      $('#chartTooltip').hidden = true;
      ctx.fillStyle = '#647988';
      ctx.textAlign = 'center';
      ctx.fillText('当前回放时刻尚无已完成 K 线', width / 2, height / 2 - 8);
      ctx.fillStyle = '#465b6a';
      ctx.fillText('推进时间后，成交数据会逐根生成', width / 2, height / 2 + 10);
      return;
    }

    const low = Math.min(...chartViewport.map((bar) => bar.low));
    const high = Math.max(...chartViewport.map((bar) => bar.high));
    const spread = Math.max(high - low, Math.max(high * 0.001, 0.01));
    const minPrice = low - spread * 0.08;
    const maxPrice = high + spread * 0.08;
    const yPrice = (price) => top + (maxPrice - price) / (maxPrice - minPrice) * plotHeight;
    const step = plotWidth / chartViewport.length;
    const candleWidth = Math.max(2, Math.min(9, step * 0.55));

    ctx.strokeStyle = 'rgba(130, 153, 170, .13)';
    ctx.fillStyle = '#718492';
    ctx.textAlign = 'right';
    for (let tick = 0; tick <= 4; tick += 1) {
      const y = top + plotHeight * tick / 4;
      const value = maxPrice - (maxPrice - minPrice) * tick / 4;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(width - right, y);
      ctx.stroke();
      ctx.fillText(value.toFixed(2), left - 7, y);
    }

    const maxVolume = Math.max(1, ...chartViewport.map((bar) => bar.volume));
    chartViewport.forEach((bar, index) => {
      const x = left + step * (index + 0.5);
      const rising = bar.close >= bar.open;
      const color = rising ? '#fa6a70' : '#35c98b';
      const openY = yPrice(bar.open);
      const closeY = yPrice(bar.close);
      const highY = yPrice(bar.high);
      const lowY = yPrice(bar.low);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, highY);
      ctx.lineTo(x, lowY);
      ctx.stroke();
      ctx.fillStyle = color;
      const bodyTop = Math.min(openY, closeY);
      const bodyHeight = Math.max(1, Math.abs(closeY - openY));
      ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
      const volumeBarHeight = Math.max(1, bar.volume / maxVolume * volumeHeight);
      ctx.globalAlpha = 0.48;
      ctx.fillRect(x - candleWidth / 2, volumeTop + volumeHeight - volumeBarHeight, candleWidth, volumeBarHeight);
      ctx.globalAlpha = 1;
    });

    const labelCount = Math.min(6, chartViewport.length);
    ctx.fillStyle = '#718492';
    ctx.textAlign = 'center';
    let lastLabel = -100;
    for (let n = 0; n < labelCount; n += 1) {
      const index = Math.round(n * (chartViewport.length - 1) / Math.max(1, labelCount - 1));
      if (index - lastLabel < 5 && n !== labelCount - 1) continue;
      const bar = chartViewport[index];
      ctx.fillText(clockFromSeconds(bar.start_seconds), left + step * (index + 0.5), height - 8);
      lastLabel = index;
    }

    const markerX = (time) => {
      const sec = typeof time === 'number' ? rawSeconds(time) : clockSeconds(time);
      if (sec == null) return null;
      let index = chartViewport.findIndex((bar) => sec >= bar.start_seconds && sec < bar.end_seconds);
      if (index < 0) return null;
      return { x: left + step * (index + 0.5), bar: chartViewport[index] };
    };
    if (state) {
      const fills = (state.fills || []).filter((fill) => Number(fill.symbol) === Number(state.symbol));
      for (const fill of fills) {
        const mapped = markerX(fill.time);
        if (!mapped) continue;
        const y = Math.max(top + 7, Math.min(bottom - 5, yPrice(fill.price)));
        ctx.fillStyle = '#3ed6c0';
        ctx.beginPath();
        ctx.arc(mapped.x, y, 6, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#06201e';
        ctx.font = '7px ui-monospace, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(fill.side === 'BUY' ? 'B' : 'S', mapped.x, y + 0.5);
        ctx.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace';
      }
      const openOrders = (state.open_orders || []).filter((order) => Number(order.symbol) === Number(state.symbol));
      for (const order of openOrders) {
        const mapped = markerX(order.created_time_text);
        if (!mapped) continue;
        ctx.fillStyle = order.side === 'BUY' ? '#35c98b' : '#fa6a70';
        ctx.textAlign = 'center';
        ctx.font = '9px ui-monospace, monospace';
        ctx.fillText('O', mapped.x, Math.max(top + 7, yPrice(order.price) - 9));
      }
    }

    if (chartHoverIndex >= 0 && chartHoverIndex < chartViewport.length) {
      const bar = chartViewport[chartHoverIndex];
      const x = left + step * (chartHoverIndex + 0.5);
      ctx.strokeStyle = 'rgba(185, 207, 219, .48)';
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      const tooltip = $('#chartTooltip');
      tooltip.textContent = `${clockFromSeconds(bar.start_seconds)}  O ${fmtPrice(bar.open)}  H ${fmtPrice(bar.high)}  L ${fmtPrice(bar.low)}  C ${fmtPrice(bar.close)}  V ${fmtQty(bar.volume)}`;
      tooltip.hidden = false;
      tooltip.style.left = `${Math.min(Math.max(4, x + 10), width - 215)}px`;
      tooltip.style.top = '7px';
    } else {
      $('#chartTooltip').hidden = true;
    }
  }

  async function loadCandles() {
    if (!meta || !els.symbol.value) return;
    try {
      const payload = await request(`/api/candles?symbol=${encodeURIComponent(els.symbol.value)}&interval=${candleInterval}`);
      candleData = payload.candles || [];
      chartHoverIndex = -1;
      drawChart();
    } catch (error) {
      candleData = [];
      drawChart();
      notify(error.message, true);
    }
  }

  function renderAccount(account) {
    setText('#availableCash', fmtMoney(account.available_cash));
    setText('#equity', fmtMoney(account.equity));
    setText('#marketValue', fmtMoney(account.total_market_value));
    setText('#unrealizedPnl', fmtMoney(account.unrealized_pnl));
    $('#unrealizedPnl').classList.toggle('pnl-negative', account.unrealized_pnl < 0);
    setText('#cash', fmtMoney(account.cash));
    setText('#reservedCash', fmtMoney(account.reserved_cash));
    const pos = account.current_position;
    setText('#positionQuantity', fmtQty(pos.quantity));
    setText('#availablePosition', fmtQty(pos.available_quantity));
    setText('#averageCost', pos.average_cost > 0 ? fmtPrice(pos.average_cost) : '—');
    setText('#realizedPnl', fmtMoney(pos.realized_pnl));
    $('#realizedPnl').classList.toggle('pnl-negative', pos.realized_pnl < 0);
    renderPositions(account.positions || []);
    const qty = Number(els.orderQuantity.value || 0);
    if (selectedSide === 'BUY') {
      els.orderCapacity.textContent = `可用 ${fmtMoney(account.available_cash)} · 按限价预留资金`;
      els.orderCapacity.classList.remove('pnl-negative');
    } else {
      els.orderCapacity.textContent = `可卖 ${fmtQty(pos.available_quantity)} 股`;
      if (qty > pos.available_quantity) els.orderCapacity.classList.add('pnl-negative');
      else els.orderCapacity.classList.remove('pnl-negative');
    }
  }

  function renderPositions(positions) {
    const body = els.positionRows;
    body.replaceChildren();
    const nonzero = positions.filter((p) => p.quantity !== 0 || p.realized_pnl !== 0);
    if (!nonzero.length) {
      body.append(emptyRow(8, '暂无持仓'));
      return;
    }
    for (const position of nonzero) {
      const tr = document.createElement('tr');
      tr.append(cell(position.symbol), cell(fmtQty(position.quantity), 'align-right'), cell(fmtQty(position.available_quantity), 'align-right'),
        cell(fmtPrice(position.average_cost), 'align-right'), cell(fmtPrice(position.last_price), 'align-right'), cell(fmtMoney(position.market_value), 'align-right'));
      const unrealized = cell(fmtMoney(position.unrealized_pnl), `align-right ${position.unrealized_pnl < 0 ? 'pnl-negative' : 'pnl-positive'}`);
      const realized = cell(fmtMoney(position.realized_pnl), `align-right ${position.realized_pnl < 0 ? 'pnl-negative' : position.realized_pnl > 0 ? 'pnl-positive' : ''}`);
      tr.append(unrealized, realized);
      body.append(tr);
    }
  }

  function renderOrders(orders) {
    els.orderRows.replaceChildren();
    els.openOrderCount.textContent = `${orders.length} 笔`;
    if (!orders.length) {
      els.orderRows.append(emptyRow(7, '暂无未完成委托'));
      return;
    }
    for (const order of orders) {
      const tr = document.createElement('tr');
      const [label, cls] = sideLabel(order.side);
      tr.append(cell(order.order_id), cell(order.symbol), cell(label, cls === 'buy' ? 'book-bid' : 'book-ask'), cell(fmtPrice(order.price), 'align-right'), cell(fmtQty(order.remaining), 'align-right'), cell(order.status === 'PARTIAL' ? '部分成交' : '已报', 'status-open'));
      const action = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'cancel-button';
      button.dataset.cancelId = order.order_id;
      button.dataset.symbol = order.symbol;
      button.textContent = '撤单';
      action.append(button);
      tr.append(action);
      els.orderRows.append(tr);
    }
  }

  function renderFills(fills) {
    els.fillRows.replaceChildren();
    els.fillCount.textContent = `${fills.length} 笔`;
    if (!fills.length) {
      els.fillRows.append(emptyRow(6, '暂无模拟成交'));
      return;
    }
    for (const fill of fills) {
      const tr = document.createElement('tr');
      const [label, cls] = sideLabel(fill.side);
      const reason = fill.reason === 'cross_current_l5' ? '对手盘 L5' : '历史逐笔';
      tr.append(cell(fill.time_text.slice(0, 12)), cell(fill.symbol), cell(label, cls === 'buy' ? 'book-bid' : 'book-ask'), cell(fmtPrice(fill.price), 'align-right'), cell(fmtQty(fill.quantity), 'align-right'), cell(reason));
      els.fillRows.append(tr);
    }
  }

  function render(nextState) {
    state = nextState;
    const market = nextState.market;
    els.clock.textContent = timeOfDay(nextState.time);
    els.session.textContent = sessionName(nextState.time, market.Session);
    els.timeline.value = String(nextState.index);
    els.timelinePercent.textContent = `${Math.round(nextState.progress * 100)}%`;
    els.jumpTime.value = timeOfDay(nextState.time);
    setText('#lastPrice', fmtPrice(market.last_price));
    renderBook(market);
    renderTrades(market);
    renderAccount(nextState.account);
    renderOrders(nextState.open_orders || []);
    renderFills(nextState.fills || []);
    drawChart();
    if (!priceEdited && market.last_price > 0) els.orderPrice.value = Number(market.last_price).toFixed(2);
    updateSubmitButton();
  }

  async function loadState() {
    const symbol = Number(els.symbol.value);
    const next = await request(`/api/state?symbol=${encodeURIComponent(symbol)}`);
    render(next);
  }

  async function seek(index, announceReset = true) {
    if (busy || !meta) return;
    setBusy(true);
    stopPlayback(false);
    try {
      const next = await request('/api/seek', {
        method: 'POST',
        body: JSON.stringify({ index, symbol: Number(els.symbol.value) }),
      });
      render(next);
      if (next.ledger_reset && announceReset) notify('已回退：模拟账户已重置');
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  function stopPlayback(updateButton = true) {
    playing = false;
    clearTimeout(playTimer);
    if (updateButton) {
      els.playIcon.textContent = '▶';
      els.playText.textContent = '播放';
    }
  }

  async function playbackStep() {
    if (!playing || busy || !state) return;
    if (state.index >= meta.timeline.length - 1) {
      stopPlayback();
      notify('已到达回放末尾');
      return;
    }
    setBusy(true);
    try {
      const next = await request('/api/seek', {
        method: 'POST',
        body: JSON.stringify({ index: state.index + 1, symbol: Number(els.symbol.value) }),
      });
      render(next);
    } catch (error) {
      stopPlayback();
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
    if (playing) playTimer = setTimeout(playbackStep, Number($('#speedSelect').value));
  }

  function startPlayback() {
    if (playing || busy) return;
    if (state.index >= meta.timeline.length - 1) {
      notify('回放已结束，请拖动时间轴选择时刻');
      return;
    }
    playing = true;
    els.playIcon.textContent = 'Ⅱ';
    els.playText.textContent = '暂停';
    playTimer = setTimeout(playbackStep, 0);
  }

  function updateSubmitButton() {
    els.submitOrder.textContent = `${selectedSide === 'BUY' ? '买入' : '卖出'}下单`;
    els.submitOrder.classList.toggle('button-buy', selectedSide === 'BUY');
    els.submitOrder.classList.toggle('button-sell', selectedSide === 'SELL');
  }

  async function submitOrder(event) {
    event.preventDefault();
    if (!state || busy) return;
    const price = Number(els.orderPrice.value);
    const quantity = Number(els.orderQuantity.value);
    if (!Number.isFinite(price) || price <= 0 || !Number.isInteger(quantity) || quantity <= 0 || quantity % meta.lot_size !== 0) {
      notify('请填写有效限价和 100 股整数倍数量', true);
      return;
    }
    setBusy(true);
    try {
      const result = await request('/api/order', {
        method: 'POST',
        body: JSON.stringify({ symbol: Number(els.symbol.value), side: selectedSide, price, quantity }),
      });
      render(result.state);
      const order = result.order;
      notify(`${order.order_id} ${order.status === 'FILLED' ? '已成交' : order.status === 'PARTIAL' ? '部分成交' : '已提交'}`);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function cancelOrder(orderId, symbol) {
    if (busy) return;
    setBusy(true);
    try {
      const result = await request('/api/cancel', { method: 'POST', body: JSON.stringify({ order_id: orderId, symbol }) });
      render(result.state);
      notify(`${orderId} 已撤单`);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  function indexForClock(value) {
    const [hour, minute, second] = value.split(':').map(Number);
    if (![hour, minute, second].every(Number.isFinite)) return null;
    const raw = hour * 10000000 + minute * 100000 + second * 1000;
    let low = 0;
    let high = meta.timeline.length - 1;
    while (low < high) {
      const mid = Math.ceil((low + high) / 2);
      if (meta.timeline[mid] <= raw) low = mid;
      else high = mid - 1;
    }
    return low;
  }

  async function jumpToClock() {
    const index = indexForClock(els.jumpTime.value);
    if (index == null) return notify('请输入有效的时刻', true);
    await seek(index);
  }

  async function resetAccount() {
    if (busy) return;
    setBusy(true);
    stopPlayback();
    try {
      const next = await request('/api/reset', { method: 'POST', body: JSON.stringify({ symbol: Number(els.symbol.value) }) });
      render(next);
      notify('模拟账户已重置，回放时刻保持不变');
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function init() {
    try {
      meta = await request('/api/meta');
      $('#sessionLabel').textContent = meta.date;
      els.timeline.min = '0';
      els.timeline.max = String(meta.timeline.length - 1);
      els.timeline.value = String(meta.current_index);
      els.symbol.replaceChildren();
      for (const symbol of meta.symbols) {
        const option = document.createElement('option');
        option.value = String(symbol);
        option.textContent = String(symbol);
        els.symbol.append(option);
      }
      els.symbol.value = String(meta.default_symbol);
      els.symbol.addEventListener('change', async () => {
        try {
          priceEdited = false;
          await loadState();
          await loadCandles();
        } catch (error) { notify(error.message, true); }
      });
      await loadState();
      await loadCandles();
    } catch (error) {
      notify(error.message || '行情数据加载失败', true);
    }
  }

  els.playButton.addEventListener('click', () => playing ? stopPlayback() : startPlayback());
  $('#backButton').addEventListener('click', () => seek(Math.max(0, Number(els.timeline.value) - 1)));
  $('#forwardButton').addEventListener('click', () => seek(Math.min(meta.timeline.length - 1, Number(els.timeline.value) + 1)));
  $('#jumpButton').addEventListener('click', jumpToClock);
  els.jumpTime.addEventListener('keydown', (event) => { if (event.key === 'Enter') jumpToClock(); });
  $('#resetButton').addEventListener('click', resetAccount);
  els.timeline.addEventListener('input', () => {
    const index = Number(els.timeline.value);
    els.timelinePercent.textContent = `${Math.round(100 * index / Math.max(1, Number(els.timeline.max)))}%`;
    clearTimeout(seekDebounce);
    seekDebounce = setTimeout(() => seek(index), 110);
  });
  els.timeline.addEventListener('change', () => {
    clearTimeout(seekDebounce);
    seek(Number(els.timeline.value));
  });
  document.querySelectorAll('.side-button').forEach((button) => {
    button.addEventListener('click', () => {
      selectedSide = button.dataset.side;
      document.querySelectorAll('.side-button').forEach((item) => item.classList.toggle('active', item === button));
      updateSubmitButton();
      if (state) renderAccount(state.account);
    });
  });
  els.orderQuantity.addEventListener('input', () => state && renderAccount(state.account));
  els.orderPrice.addEventListener('input', () => { priceEdited = true; });
  $('#orderForm').addEventListener('submit', submitOrder);
  els.orderRows.addEventListener('click', (event) => {
    const button = event.target.closest('[data-cancel-id]');
    if (button) cancelOrder(button.dataset.cancelId, Number(button.dataset.symbol));
  });

  document.querySelectorAll('.chart-interval').forEach((button) => {
    button.addEventListener('click', async () => {
      candleInterval = Number(button.dataset.interval);
      document.querySelectorAll('.chart-interval').forEach((item) => item.classList.toggle('active', item === button));
      await loadCandles();
    });
  });
  $('#klineCanvas').addEventListener('mousemove', (event) => {
    if (!chartViewport.length) return;
    const rect = $('#chartStage').getBoundingClientRect();
    const left = 54;
    const right = 13;
    const x = event.clientX - rect.left;
    const plotWidth = rect.width - left - right;
    const step = plotWidth / chartViewport.length;
    chartHoverIndex = Math.max(0, Math.min(chartViewport.length - 1, Math.floor((x - left) / step)));
    drawChart();
  });
  $('#klineCanvas').addEventListener('mouseleave', () => {
    chartHoverIndex = -1;
    drawChart();
  });
  window.addEventListener('resize', () => drawChart());

  init();
})();
