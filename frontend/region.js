/* PDF 框选提表（T9）
 *
 * 独立脚本，不改 app.js：在图纸上拖一个框 → POST /api/jobs/{id}/pdf_region
 * → 预览表格 → 复制 / 下载 CSV。只读，不写入任务数据。
 */
(() => {
  'use strict';

  const TOKEN_KEY = 'dgzh_auth_token';
  const readToken = () => {
    try {
      return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || '';
    } catch (_) {
      return '';
    }
  };
  const currentJobId = () => {
    const m = location.hash.match(/^#\/job\/([0-9a-f]{6,32})/i);
    return m ? m[1] : '';
  };
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toast = (msg) => {
    const t = document.getElementById('toast');
    if (!t) { alert(msg); return; }
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { t.hidden = true; }, 3200);
  };

  const btn = document.getElementById('btnPdfRegion');
  if (!btn) return;

  btn.addEventListener('click', () => {
    const jobId = currentJobId();
    if (!jobId) { toast('请先打开一个任务再框选提表'); return; }
    const token = readToken();
    if (!token) { toast('请先登录'); return; }
    openModal(jobId, token);
  });

  function openModal(jobId, token) {
    const root = document.createElement('div');
    root.id = 'pdfRegionModal';
    root.className = 'fixed inset-0 z-[9999] bg-slate-900/75 backdrop-blur-sm flex items-center justify-center p-4';
    root.innerHTML = `
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden">
        <div class="flex items-center justify-between px-5 py-3 border-b border-slate-200 shrink-0">
          <div>
            <h3 class="text-base font-bold text-slate-900">框选提表</h3>
            <p class="text-xs text-slate-500 mt-0.5">在图纸上按住鼠标拖一个矩形，松开即提取该区域的表格文字（只读预览，不改任务数据）。</p>
          </div>
          <div class="flex items-center gap-2">
            <label class="text-xs text-slate-500">页码</label>
            <input id="prPage" type="number" min="1" value="1" class="w-16 h-8 px-2 rounded border border-slate-300 text-sm">
            <button id="prClose" class="material-symbols-outlined w-8 h-8 rounded hover:bg-slate-100 text-slate-500" type="button">close</button>
          </div>
        </div>
        <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2">
          <div class="min-h-0 overflow-auto bg-slate-100 p-3 flex items-start justify-center">
            <div id="prStage" class="relative inline-block select-none cursor-crosshair shadow-md">
              <img id="prImage" alt="图纸页面" class="block max-h-[72vh] w-auto" draggable="false">
              <div id="prSel" class="absolute hidden border-2 border-dashed border-blue-600 bg-blue-600/15 pointer-events-none"></div>
            </div>
          </div>
          <div class="min-h-0 flex flex-col border-l border-slate-200">
            <div id="prStatus" class="px-4 py-2 text-xs text-slate-500 border-b border-slate-200">拖拽框选后自动提取</div>
            <div id="prResult" class="flex-1 min-h-0 overflow-auto p-3 text-xs text-slate-500">暂无结果</div>
          </div>
        </div>
        <div class="px-5 py-3 border-t border-slate-200 flex items-center justify-between shrink-0">
          <span class="text-[11px] text-slate-400">来源会标注为 PDF 表格 / PDF 文字 / OCR；OCR 结果务必人工核对。</span>
          <div class="flex items-center gap-2">
            <button id="prCopy" class="h-8 px-3 rounded border border-slate-300 text-xs text-slate-600 hover:bg-slate-50" type="button" disabled>复制</button>
            <button id="prCsv" class="h-8 px-3 rounded bg-slate-900 text-white text-xs hover:bg-slate-700" type="button" disabled>下载 CSV</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(root);

    const stage = root.querySelector('#prStage');
    const img = root.querySelector('#prImage');
    const sel = root.querySelector('#prSel');
    const status = root.querySelector('#prStatus');
    const result = root.querySelector('#prResult');
    const pageInput = root.querySelector('#prPage');
    const copyBtn = root.querySelector('#prCopy');
    const csvBtn = root.querySelector('#prCsv');
    let rows = [];
    let dragStart = null;
    let lastRect = null;
    let loading = false;

    const close = () => {
      document.removeEventListener('keydown', onKey);
      root.remove();
    };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    root.querySelector('#prClose').addEventListener('click', close);
    root.addEventListener('click', (e) => { if (e.target === root) close(); });

    const loadPage = () => {
      const page = Math.max(1, Number(pageInput.value) || 1);
      lastRect = null;
      rows = [];
      sel.classList.add('hidden');
      result.textContent = '加载图纸…';
      status.textContent = `正在加载第 ${page} 页…`;
      img.onload = () => { status.textContent = '在左侧图纸上拖拽框选要提取的表格区域'; };
      img.onerror = () => { status.textContent = '图纸页面加载失败'; result.textContent = '无法加载该页，请确认任务图纸存在。'; };
      img.src = `/api/jobs/${encodeURIComponent(jobId)}/page/${page}?token=${encodeURIComponent(token)}&t=${Date.now()}`;
    };
    pageInput.addEventListener('change', loadPage);
    loadPage();

    const point = (e) => {
      const r = stage.getBoundingClientRect();
      return {
        x: Math.min(Math.max(e.clientX - r.left, 0), r.width),
        y: Math.min(Math.max(e.clientY - r.top, 0), r.height),
        w: r.width,
        h: r.height,
      };
    };

    stage.addEventListener('pointerdown', (e) => {
      if (loading || e.button !== 0) return;
      e.preventDefault();
      dragStart = point(e);
      sel.style.left = `${dragStart.x}px`;
      sel.style.top = `${dragStart.y}px`;
      sel.style.width = '0px';
      sel.style.height = '0px';
      sel.classList.remove('hidden');
      stage.setPointerCapture(e.pointerId);
    });

    stage.addEventListener('pointermove', (e) => {
      if (!dragStart) return;
      const p = point(e);
      const x = Math.min(dragStart.x, p.x);
      const y = Math.min(dragStart.y, p.y);
      sel.style.left = `${x}px`;
      sel.style.top = `${y}px`;
      sel.style.width = `${Math.abs(p.x - dragStart.x)}px`;
      sel.style.height = `${Math.abs(p.y - dragStart.y)}px`;
    });

    stage.addEventListener('pointerup', (e) => {
      if (!dragStart) return;
      const p = point(e);
      const x0 = Math.min(dragStart.x, p.x);
      const y0 = Math.min(dragStart.y, p.y);
      const x1 = Math.max(dragStart.x, p.x);
      const y1 = Math.max(dragStart.y, p.y);
      const w = dragStart.w;
      const h = dragStart.h;
      dragStart = null;
      if (x1 - x0 < 6 || y1 - y0 < 6) {
        sel.classList.add('hidden');
        status.textContent = '框太小，请重新拖拽';
        return;
      }
      extract({
        x0: x0 / w, y0: y0 / h, x1: x1 / w, y1: y1 / h,
      });
    });

    async function extract(rect) {
      if (loading) return;
      loading = true;
      lastRect = rect;
      const page = Math.max(1, Number(pageInput.value) || 1);
      status.textContent = '正在提取该区域文字…';
      result.textContent = '处理中…';
      copyBtn.disabled = true;
      csvBtn.disabled = true;
      try {
        const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/pdf_region`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ page, rect, use_ocr: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `请求失败 (${res.status})`);
        rows = Array.isArray(data.rows) ? data.rows : [];
        if (data.page_count) pageInput.max = String(data.page_count);
        const sourceLabel = { PDF_TABLE: 'PDF 矢量表格', PDF_TEXT: 'PDF 文字层', OCR: 'OCR 识别' }[data.source] || '未识别';
        status.textContent = `来源：${sourceLabel} · ${data.cell_count || 0} 个单元格${(data.warnings || []).length ? ' · ' + data.warnings.join('；') : ''}`;
        renderRows();
      } catch (err) {
        status.textContent = err.message || '提取失败';
        result.textContent = '提取失败，请重新框选或换一页。';
      } finally {
        loading = false;
      }
    }

    function renderRows() {
      if (!rows.length) {
        result.innerHTML = '<div class="p-2 text-slate-500">该区域没有提取到文字。若图纸是扫描件，请确认服务器已安装 tesseract 中文语言包。</div>';
        copyBtn.disabled = true;
        csvBtn.disabled = true;
        return;
      }
      const maxCols = Math.max(...rows.map((r) => r.length), 1);
      const body = rows.map((row) => {
        const cells = Array.from({ length: maxCols }, (_, i) => row[i] || { text: '', source: '' });
        return `<tr class="border-b border-slate-100">${cells.map((c) => (
          `<td class="px-2 py-1 align-top text-slate-800" title="${esc(c.source || '')}">${esc(c.text || '')}</td>`
        )).join('')}</tr>`;
      }).join('');
      result.innerHTML = `<table class="w-full border-collapse text-xs"><tbody>${body}</tbody></table>`;
      copyBtn.disabled = false;
      csvBtn.disabled = false;
    }

    const tsv = () => rows.map((r) => r.map((c) => c.text || '').join('\t')).join('\n');

    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(tsv());
        toast('已复制到剪贴板');
      } catch (_) {
        toast('复制失败，请手动选择表格文字');
      }
    });

    csvBtn.addEventListener('click', () => {
      const csv = rows.map((r) => r.map((c) => `"${String(c.text || '').replace(/"/g, '""')}"`).join(',')).join('\r\n');
      const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${jobId}-框选提表-第${Math.max(1, Number(pageInput.value) || 1)}页.csv`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
  }
})();
