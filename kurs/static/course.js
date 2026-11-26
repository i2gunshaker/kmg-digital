(function () {
  "use strict";

  var dataEl = document.getElementById("course-data");
  if (!dataEl) return;
  var DATA = JSON.parse(dataEl.textContent);
  var STORAGE_KEY = "kurs_progress::" + DATA.chapter + "::v1";

  var memoryState = {
    lastStepId: DATA.steps[0] ? DATA.steps[0].id : null,
    doneSteps: {},
    answers: {},
    cards: {},
    theme: "auto"
  };

  function storageAvailable() {
    try {
      var k = "__kurs_test__";
      window.localStorage.setItem(k, "1");
      window.localStorage.removeItem(k);
      return true;
    } catch (e) {
      return false;
    }
  }

  var HAS_LS = storageAvailable();
  var saveStatusEl = document.getElementById("save-status");

  function updateSaveStatus(msg) {
    if (saveStatusEl) saveStatusEl.textContent = msg;
  }

  function loadState() {
    if (!HAS_LS) {
      updateSaveStatus("прогресс живёт до закрытия вкладки, сохраните его в файл");
      return memoryState;
    }
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        updateSaveStatus("прогресс сохраняется в браузере");
        return memoryState;
      }
      var parsed = JSON.parse(raw);
      if (parsed && parsed.chapter === DATA.chapter) {
        memoryState = Object.assign(memoryState, parsed);
      }
      updateSaveStatus("прогресс сохранён в браузере");
    } catch (e) {
      updateSaveStatus("хранилище не читается, сохраните прогресс в файл");
    }
    return memoryState;
  }

  function saveState() {
    memoryState.chapter = DATA.chapter;
    if (!HAS_LS) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(memoryState));
      updateSaveStatus("прогресс сохранён в браузере");
    } catch (e) {
      updateSaveStatus("хранилище заполнено, сохраните прогресс в файл");
    }
  }

  var state = loadState();

  // ---------------- Тема ----------------
  var themeSelect = document.getElementById("theme-select");
  function applyTheme(val) {
    state.theme = val || "auto";
    if (state.theme === "light" || state.theme === "dark") {
      document.documentElement.setAttribute("data-theme", state.theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    if (themeSelect) themeSelect.value = state.theme;
    saveState();
  }
  if (themeSelect) {
    themeSelect.addEventListener("change", function () {
      applyTheme(themeSelect.value);
    });
  }
  applyTheme(state.theme || "auto");

  // ---------------- Термины ----------------
  var pop = document.getElementById("term-pop");
  function hideTerm() { if (pop) pop.hidden = true; }
  function showTerm(el) {
    if (!pop) return;
    pop.textContent = "";
    var b = document.createElement("b");
    b.textContent = el.getAttribute("data-term") + ". ";
    pop.appendChild(b);
    pop.appendChild(document.createTextNode(el.getAttribute("data-def")));
    pop.hidden = false;
  }
  document.querySelectorAll(".term").forEach(function (el) {
    el.addEventListener("mouseenter", function () { showTerm(el); });
    el.addEventListener("mouseleave", hideTerm);
    el.addEventListener("focus", function () { showTerm(el); });
    el.addEventListener("blur", hideTerm);
    el.addEventListener("click", function () { showTerm(el); });
  });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") hideTerm(); });

  // ---------------- Вкладки и шаги ----------------
  var tabButtons = Array.from(document.querySelectorAll(".tab-btn[data-tab]"));
  var tabPanels = Array.from(document.querySelectorAll(".tab-panel"));
  var stepSections = Array.from(document.querySelectorAll(".step-section"));
  var sidebarEl = document.getElementById("sidebar-nav");
  var drawerBtn = document.getElementById("drawer-toggle");

  if (drawerBtn && sidebarEl) {
    drawerBtn.addEventListener("click", function () {
      var open = sidebarEl.classList.toggle("open");
      drawerBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function switchTab(tabId) {
    tabButtons.forEach(function (b) {
      var active = b.getAttribute("data-tab") === tabId;
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
    tabPanels.forEach(function (p) {
      p.hidden = p.id !== "tab-" + tabId;
    });
    hideTerm();
  }

  tabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      switchTab(btn.getAttribute("data-tab"));
    });
  });

  function updateProgressUI() {
    var total = DATA.steps.length || 1;
    var doneCount = Object.keys(state.doneSteps).filter(function (k) { return state.doneSteps[k]; }).length;
    var pct = Math.round((doneCount / total) * 100);
    var fill = document.getElementById("progress-fill");
    var label = document.getElementById("progress-label");
    if (fill) fill.style.width = pct + "%";
    if (label) label.textContent = "Пройдено " + doneCount + " из " + total;
    document.querySelectorAll(".path-item").forEach(function (b) {
      var sid = b.getAttribute("data-step");
      b.classList.toggle("active", sid === state.lastStepId);
      var mark = b.querySelector(".path-done");
      if (mark) mark.textContent = state.doneSteps[sid] ? "✓" : "";
      b.setAttribute("aria-current", sid === state.lastStepId ? "step" : "false");
    });
    document.querySelectorAll("[data-mark-done]").forEach(function (b) {
      b.textContent = state.doneSteps[b.getAttribute("data-mark-done")] ? "Пройден ✓" : "Отметить пройденным";
    });
  }

  function showStep(stepId, focusHeading) {
    if (!stepId || !document.getElementById("step-" + stepId)) {
      stepId = DATA.steps[0] ? DATA.steps[0].id : null;
    }
    if (!stepId) return;
    switchTab("step");
    stepSections.forEach(function (sec) {
      sec.hidden = sec.id !== "step-" + stepId;
    });
    state.lastStepId = stepId;
    saveState();
    updateProgressUI();
    if (window.location.hash !== "#step-" + stepId) {
      try {
        history.replaceState(null, "", "#step-" + stepId);
      } catch (e) {}
    }
    if (sidebarEl && sidebarEl.classList.contains("open")) {
      sidebarEl.classList.remove("open");
      if (drawerBtn) drawerBtn.setAttribute("aria-expanded", "false");
    }
    if (focusHeading) {
      var sec = document.getElementById("step-" + stepId);
      var h = sec && sec.querySelector("h2");
      if (h) {
        h.setAttribute("tabindex", "-1");
        h.focus({ preventScroll: true });
      }
      var mainEl = document.getElementById("main-content");
      if (mainEl) window.scrollTo(0, Math.max(0, mainEl.getBoundingClientRect().top + window.scrollY - 12));
      var item = document.querySelector('.path-item.active');
      if (item && sidebarEl) {
        var top = item.offsetTop - sidebarEl.offsetTop;
        if (top < sidebarEl.scrollTop || top > sidebarEl.scrollTop + sidebarEl.clientHeight - 60) sidebarEl.scrollTop = Math.max(0, top - 80);
      }
    }
  }

  document.querySelectorAll("[data-goto-step]").forEach(function (el) {
    el.addEventListener("click", function (ev) {
      ev.preventDefault();
      var sid = el.getAttribute("data-goto-step");
      showStep(sid, true);
    });
  });

  document.querySelectorAll("[data-mark-done]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sid = btn.getAttribute("data-mark-done");
      state.doneSteps[sid] = !state.doneSteps[sid];
      saveState();
      updateProgressUI();
    });
  });

  // ---------------- Рендер формул локальным KaTeX ----------------
  if (window.katex) {
    document.querySelectorAll(".math-tex").forEach(function (el) {
      var tex = el.getAttribute("data-tex") || el.textContent;
      var display = el.getAttribute("data-display") === "true";
      try {
        window.katex.render(tex.replace(/\\tag\*?\s*\{([^{}]*)\}/g, "\\qquad ($1)"), el, { displayMode: display, throwOnError: false, strict: "ignore" });
        if (!display && (tex.length > 110 || /\\begin/.test(tex))) el.classList.add("math-block");
        else if (!display && tex.length > 45) el.classList.add("math-wide");
      } catch (e) {}
    });
  }

  // ---------------- Задания (5 типов) ----------------
  function normText(s) {
    return String(s || "").trim().toLowerCase().replace(/ё/g, "е").replace(/(\d),(\d)/g, "$1.$2").replace(/[.;:!?]+$/, "").replace(/\s+/g, " ");
  }

  function parseNumeric(raw) {
    var s = String(raw || "").trim().replace(/\s+/g, "").replace(",", ".");
    if (!s || !/^[+-]?\d+(?:\.\d+)?$/.test(s)) return NaN;
    return Number(s);
  }

  function initOrderLists() {
    document.querySelectorAll(".order-list").forEach(function (ul) {
      if (window.Sortable) {
        new window.Sortable(ul, {
          handle: ".drag-handle",
          animation: 0,
          forceFallback: true,
          fallbackOnBody: true
        });
      }
      ul.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-move]");
        if (!btn) return;
        var li = btn.closest(".order-item");
        if (!li) return;
        if (btn.getAttribute("data-move") === "up" && li.previousElementSibling) {
          ul.insertBefore(li, li.previousElementSibling);
        } else if (btn.getAttribute("data-move") === "down" && li.nextElementSibling) {
          ul.insertBefore(li.nextElementSibling, li);
        }
        btn.focus();
      });
    });
  }
  initOrderLists();

  function formatCorrectAnswer(task) {
    if (task.type === "single") {
      var opt = (task.options || []).find(function (o) { return o.id === task.answer; });
      return opt ? opt.text : String(task.answer);
    }
    if (task.type === "multiple") {
      var ids = new Set(task.answer || []);
      return (task.options || []).filter(function (o) { return ids.has(o.id); }).map(function (o) { return o.text; }).join("; ");
    }
    if (task.type === "order") {
      var byId = {};
      (task.options || []).forEach(function (o) { byId[o.id] = o.text; });
      return (task.answer || []).map(function (id, i) { return (i + 1) + ". " + (byId[id] || id); }).join(" → ");
    }
    if (task.type === "gap") {
      return Array.isArray(task.answer) ? task.answer.join(", ") : String(task.answer);
    }
    if (task.type === "calc") {
      return String(task.answer) + (task.unit ? " " + task.unit : "");
    }
    return String(task.answer);
  }

  document.querySelectorAll(".task-card").forEach(function (card) {
    var tid = card.getAttribute("data-task-id");
    var task = DATA.tasksById[tid];
    if (!task) return;
    var btn = card.querySelector(".check-task-btn");
    var fb = card.querySelector(".task-feedback");
    if (!btn || !fb) return;

    btn.addEventListener("click", function () {
      var ok = false;
      var msg = "";
      if (task.type === "single") {
        var sel = card.querySelector('input[type="radio"]:checked');
        if (!sel) {
          fb.className = "task-feedback bad";
          fb.textContent = "Выберите один вариант ответа.";
          fb.hidden = false;
          return;
        }
        ok = sel.value === task.answer;
      } else if (task.type === "multiple") {
        var picked = Array.from(card.querySelectorAll('input[type="checkbox"]:checked')).map(function (el) { return el.value; }).sort();
        if (!picked.length) {
          fb.className = "task-feedback bad";
          fb.textContent = "Отметьте хотя бы один вариант.";
          fb.hidden = false;
          return;
        }
        var expected = (task.answer || []).slice().sort();
        ok = picked.length === expected.length && picked.every(function (v, i) { return v === expected[i]; });
      } else if (task.type === "order") {
        var cur = Array.from(card.querySelectorAll(".order-item")).map(function (li) { return li.getAttribute("data-opt-id"); });
        ok = cur.length === task.answer.length && cur.every(function (v, i) { return v === task.answer[i]; });
      } else if (task.type === "gap") {
        var inputs = Array.from(card.querySelectorAll(".gap-input"));
        var vals = inputs.map(function (inp) { return normText(inp.value); });
        if (vals.some(function (v) { return !v; })) {
          fb.className = "task-feedback bad";
          fb.textContent = "Заполните все пропуски.";
          fb.hidden = false;
          return;
        }
        var ansArr = Array.isArray(task.answer) ? task.answer : [task.answer];
        ok = vals.length === ansArr.length && vals.every(function (v, i) { return v === normText(ansArr[i]); });
      } else if (task.type === "calc") {
        var cinp = card.querySelector(".calc-input");
        var x = parseNumeric(cinp ? cinp.value : "");
        if (!Number.isFinite(x)) {
          fb.className = "task-feedback bad";
          fb.textContent = "Введите корректное число (допустимы точка или запятая).";
          fb.hidden = false;
          return;
        }
        var y = Number(task.answer);
        var tol = task.tolerance != null ? Number(task.tolerance) : Math.max(0.05, 0.02 * Math.abs(y));
        ok = Math.abs(x - y) <= tol;
      }

      state.answers[tid] = ok;
      saveState();
      fb.className = "task-feedback " + (ok ? "ok" : "bad");
      var claimRef = (task.claim_ids && task.claim_ids[0]) ? task.claim_ids[0] : "";
      var page = DATA.claimPages && DATA.claimPages[claimRef];
      var correct = formatCorrectAnswer(task);
      fb.innerHTML =
        "<p><strong>" + (ok ? "Верно." : "Неверно.") + "</strong> " +
        (ok ? "" : "Правильный ответ: " + escapeHtml(correct) + (/[.!?]$/.test(correct) ? " " : ". ")) +
        escapeHtml(task.explanation || "") +
        (claimRef && document.getElementById("claim-" + claimRef) ? ' <a href="#claim-' + escapeAttr(claimRef) + '" data-jump-claim="' + escapeAttr(claimRef) + '">Место в книге' + (page ? ", стр. " + page : "") + "</a>" : "") + "</p>";
      fb.hidden = false;
    });
  });

  // Переход к утверждению: раскрываем цитату и подсвечиваем
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("[data-jump-claim]");
    if (!a) return;
    ev.preventDefault();
    var el = document.getElementById("claim-" + a.getAttribute("data-jump-claim"));
    if (!el) return;
    var step = el.closest(".step-section");
    if (step && step.hidden) showStep(step.id.replace(/^step-/, ""), false);
    for (var d = el.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
    var inner = el.querySelector("details");
    if (inner) inner.open = true;
    el.scrollIntoView({ block: "center" });
    el.classList.add("hl-claim");
    window.setTimeout(function () { el.classList.remove("hl-claim"); }, 2400);
  });

  // Кадры рисунка: ползунок или кнопки
  document.querySelectorAll(".ill-card[data-frames]").forEach(function (fig) {
    var frames = Array.from(fig.querySelectorAll(".ill-frame"));
    var value = fig.querySelector(".frames-value");
    function show(i, label) {
      frames.forEach(function (f, k) { f.hidden = k !== i; });
      if (value) value.textContent = label;
    }
    var range = fig.querySelector(".frame-range");
    if (range) {
      var labels = range.getAttribute("data-labels").split("|");
      range.addEventListener("input", function () { show(Number(range.value), labels[Number(range.value)]); });
    }
    var btns = Array.from(fig.querySelectorAll(".frame-btn"));
    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        btns.forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        show(Number(b.getAttribute("data-frame-to")), b.getAttribute("data-label"));
      });
    });
  });

  // Карта связей
  document.querySelectorAll(".arc-item").forEach(function (b) {
    b.addEventListener("click", function () {
      var id = b.getAttribute("data-node");
      var was = b.classList.contains("on");
      var near = {};
      document.querySelectorAll(".arcs path").forEach(function (p) {
        var hit = !was && (p.getAttribute("data-from") === id || p.getAttribute("data-to") === id);
        p.classList.toggle("on", hit);
        p.classList.toggle("off", !was && !hit);
        if (hit) { near[p.getAttribute("data-from")] = 1; near[p.getAttribute("data-to")] = 1; }
      });
      document.querySelectorAll(".arc-item").forEach(function (x) { x.classList.toggle("on", !was && (x === b || !!near[x.getAttribute("data-node")])); });
    });
  });

  // Поиск по глоссарию
  var gsearch = document.getElementById("glossary-search");
  if (gsearch) gsearch.addEventListener("input", function () {
    var q = normText(gsearch.value);
    document.querySelectorAll("#glossary-list .term-row").forEach(function (r) { r.hidden = !!q && normText(r.textContent).indexOf(q) < 0; });
  });

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function escapeAttr(s) { return escapeHtml(s); }

  // ---------------- Карточки повторения ----------------
  var cardIdx = 0;
  var cardFlipped = false;
  var cardTitleEl = document.getElementById("flash-title");
  var cardBodyEl = document.getElementById("flash-body");
  var cardBackEl = document.getElementById("flash-back");
  var cardStatEl = document.getElementById("flash-status");

  function renderCard() {
    if (!DATA.cards.length || !cardTitleEl) return;
    var c = DATA.cards[cardIdx % DATA.cards.length];
    var st = state.cards[c.id] || "новая";
    cardTitleEl.textContent = c.front;
    cardBodyEl.textContent = "Карточка " + (cardIdx % DATA.cards.length + 1) + " из " + DATA.cards.length + ", " + st;
    cardBackEl.textContent = c.back + " Стр. " + c.pages + ".";
    var flip = document.getElementById("flash-flip");
    if (flip) flip.textContent = cardFlipped ? "Скрыть ответ" : "Показать ответ";
    cardBackEl.hidden = !cardFlipped;
    if (cardStatEl) {
      var known = DATA.cards.filter(function (x) { return state.cards[x.id] === "знаю"; }).length;
      cardStatEl.textContent = "Освоено: " + known + " из " + DATA.cards.length;
    }
  }

  var flipBtn = document.getElementById("flash-flip");
  var knowBtn = document.getElementById("flash-know");
  var repBtn = document.getElementById("flash-repeat");
  if (flipBtn) flipBtn.addEventListener("click", function () { cardFlipped = !cardFlipped; renderCard(); });
  if (knowBtn) knowBtn.addEventListener("click", function () {
    var c = DATA.cards[cardIdx % DATA.cards.length];
    state.cards[c.id] = "знаю";
    saveState();
    cardIdx = (cardIdx + 1) % DATA.cards.length;
    cardFlipped = false;
    renderCard();
  });
  if (repBtn) repBtn.addEventListener("click", function () {
    var c = DATA.cards[cardIdx % DATA.cards.length];
    state.cards[c.id] = "повторить";
    saveState();
    cardIdx = (cardIdx + 1) % DATA.cards.length;
    cardFlipped = false;
    renderCard();
  });
  renderCard();

  // ---------------- Интерактив Volve ----------------
  document.querySelectorAll(".volve-box").forEach(function (box) {
    if (!DATA.volve) return;
    var ivSelect = box.querySelector(".volve-interval");
    var slider = box.querySelector(".volve-depth");
    var depthLbl = box.querySelector(".volve-depth-val");
    var mGr = box.querySelector(".m-gr");
    var mRt = box.querySelector(".m-rt");
    var mPhif = box.querySelector(".m-phif");
    var mSw = box.querySelector(".m-sw");
    var mFlag = box.querySelector(".m-flag");
    var verdictBtn = box.querySelector(".volve-check-btn");
    var verdictFb = box.querySelector(".volve-feedback");

    function updateReadout() {
      var ivId = ivSelect ? ivSelect.value : "A";
      var iv = DATA.volve.intervals.find(function (x) { return x.id === ivId; }) || DATA.volve.intervals[0];
      var pts = iv.points || [];
      var idx = slider ? Math.min(pts.length - 1, Math.max(0, Number(slider.value) || 0)) : 0;
      var pt = pts[idx] || {};
      var ru = function (v, unit) { return v == null ? "нет данных" : String(v).replace(".", ",") + (unit || ""); };
      if (depthLbl) depthLbl.textContent = ru(pt.depth_m, " м");
      if (mGr) mGr.textContent = ru(pt.GR, " API");
      if (mRt) mRt.textContent = ru(pt.RT, " Ом·м");
      if (mPhif) mPhif.textContent = ru(pt.PHIF);
      if (mSw) mSw.textContent = ru(pt.SW);
      if (mFlag) mFlag.textContent = pt.SAND_FLAG === 1 ? "1, да" : "0, нет";
    }

    if (ivSelect) ivSelect.addEventListener("change", updateReadout);
    if (slider) slider.addEventListener("input", updateReadout);
    updateReadout();

    if (verdictBtn && verdictFb) {
      verdictBtn.addEventListener("click", function () {
        var picked = box.querySelector('input[name="volve-pick"]:checked');
        if (!picked) {
          verdictFb.className = "task-feedback bad";
          verdictFb.textContent = "Выберите один из трёх интервалов (A, B или C).";
          verdictFb.hidden = false;
          return;
        }
        var ok = picked.value === "A";
        verdictFb.className = "volve-feedback task-feedback " + (ok ? "ok" : "bad");
        verdictFb.textContent = ok
          ? "Верно. В интервале A пористость 0,228, глинистость 0,029, сопротивление 37,3 Ом·м и водонасыщенность 0,131: коллектор с нефтью."
          : "Неверно. В B сопротивление 7,43 Ом·м, но пористость 0,02: это плотный неколлектор. В C пористость 0,157, но сопротивление 0,57 Ом·м и водонасыщенность 1: вода. Нефть даст A.";
        verdictFb.hidden = false;
      });
    }
  });

  // ---------------- Экспорт / импорт и печать ----------------
  var exportBtn = document.getElementById("export-progress");
  var importInput = document.getElementById("import-progress");
  var printBtn = document.getElementById("print-summary");

  if (exportBtn) {
    exportBtn.addEventListener("click", function () {
      var blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = DATA.chapter + "_progress.json";
      a.click();
      URL.revokeObjectURL(a.href);
    });
  }
  if (importInput) {
    importInput.addEventListener("change", function () {
      var f = importInput.files && importInput.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        try {
          var parsed = JSON.parse(String(reader.result || "{}"));
          if (parsed.chapter && parsed.chapter !== DATA.chapter) {
            updateSaveStatus("это файл другой главы");
            return;
          }
          state = Object.assign(memoryState, parsed);
          saveState();
          applyTheme(state.theme || "auto");
          showStep(state.lastStepId, false);
          renderCard();
          updateSaveStatus("прогресс загружен из файла");
        } catch (e) {
          updateSaveStatus("файл прогресса не читается");
        }
      };
      reader.readAsText(f);
    });
  }
  if (printBtn) {
    printBtn.addEventListener("click", function () {
      switchTab("summary");
      window.print();
    });
  }

  // ---------------- Субтитры видео через VTTCue ----------------
  document.querySelectorAll("video[data-subtitles-id]").forEach(function (vid) {
    var sid = vid.getAttribute("data-subtitles-id");
    var cues = (DATA.subtitles && DATA.subtitles[sid]) || [];
    if (!cues.length || !vid.addTextTrack || !window.VTTCue) return;
    var track = vid.addTextTrack("subtitles", "Русский", "ru");
    // Подписи уже впечатаны в кадр; дорожка — для чтения с экрана и включения вручную, иначе текст двоится
    track.mode = "hidden";
    cues.forEach(function (c) {
      track.addCue(new window.VTTCue(c.t_start, c.t_end, c.text));
    });
  });

  // ---------------- Начальное открытие шага ----------------
  var hashStep = (window.location.hash || "").replace(/^#step-/, "");
  if (hashStep && document.getElementById("step-" + hashStep)) {
    showStep(hashStep, false);
  } else {
    showStep(state.lastStepId, false);
  }
})();
