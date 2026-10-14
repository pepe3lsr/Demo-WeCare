/* Tótem WeCare: cámara → identificación → contexto → conversación → acción. */
(() => {
  const app = document.getElementById("app");
  const VOZ = app.dataset.voz === "si";
  const FACIAL = app.dataset.facial === "si";
  const KIOSK_ID = "kiosk-1";
  const OBSERVE_MS = 1200;

  const $ = (id) => document.getElementById(id);
  const idle = $("idle"), sessionScreen = $("session");
  const cam = $("cam"), canvas = $("frame"), ctx = canvas.getContext("2d");

  let session = null;          // {id, nombre, ...}
  let observing = false;
  let facesSince = null;       // cuándo empezó a haber caras sin reconocer
  let audioEl = null;          // TTS en curso
  let recorder = null, chunks = [];
  let busy = false;

  // ------------------------------------------------------------------ utilidades
  const horaEl = $("hora");
  function tick() { horaEl.textContent = new Date().toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", hour12: false }); }
  tick(); setInterval(tick, 10000);

  function saludoHora() {
    const h = new Date().getHours();
    return h < 12 ? "buen día" : h < 19 ? "buenas tardes" : "buenas noches";
  }
  $("idle-title").textContent = `Hola, ${saludoHora()}.`;

  function setEstado(txt, cls) {
    const el = $("estado-voz");
    el.textContent = txt || "";
    el.className = "estado-voz" + (cls ? " " + cls : "");
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) { /* vacío */ }
      const err = new Error(msg); err.status = res.status; throw err;
    }
    return res;
  }
  const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json());

  // ------------------------------------------------------------------ cámara
  async function startCamera() {
    if (!navigator.mediaDevices) return;
    try {
      cam.srcObject = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
      observing = true;
      setTimeout(observeLoop, 800);
    } catch (e) {
      console.warn("Sin cámara:", e);
      $("ojo").title = "Sin cámara";
    }
  }

  function captureBlob() {
    return new Promise((resolve) => {
      if (!cam.videoWidth) return resolve(null);
      ctx.drawImage(cam, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(resolve, "image/jpeg", 0.75);
    });
  }

  async function observeLoop() {
    if (!observing) return;
    try {
      const blob = await captureBlob();
      if (blob) {
        const fd = new FormData();
        fd.append("kiosk_id", KIOSK_ID);
        if (session) fd.append("session_id", session.id);
        fd.append("image", blob, "frame.jpg");
        const r = await (await api("/api/observe", { method: "POST", body: fd })).json();
        handleObservation(r);
      }
    } catch (e) {
      console.warn("observe:", e.message);
    } finally {
      setTimeout(observeLoop, OBSERVE_MS);
    }
  }

  const DEBUG = new URLSearchParams(location.search).has("debug");
  if (DEBUG) { const d = document.createElement("div"); d.id = "debug"; d.className = "debug"; document.body.appendChild(d); }

  const octx = $("cam-overlay").getContext("2d");
  function drawFace(r) {
    octx.clearRect(0, 0, 640, 480);
    const label = $("cam-label");
    if (!r.face_box) { label.textContent = FACIAL ? "Buscando una cara…" : ""; label.className = "cam-label"; return; }
    let color = "#d08a00", cls = "busca", text = "Reconociendo…";
    if (session && session.nombre) { color = "#2e7d4f"; cls = "ok"; text = `Hola, ${session.nombre}`; }
    else if (session) { color = "#2e7d4f"; cls = "ok"; text = "Modo general"; }
    else if (r.n_faces > 1) { color = "#b3261e"; cls = "varias"; text = "Veo varias personas"; }
    else if (r.candidate) { color = "#2e7d4f"; cls = "ok"; text = "¡Casi! un segundo…"; }
    else if (!FACIAL) { text = "Cara detectada"; }
    else { text = "No te reconozco todavía"; }
    const [x1, y1, x2, y2] = r.face_box;
    octx.lineWidth = 5; octx.strokeStyle = color; octx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    label.textContent = text; label.className = "cam-label " + cls;
  }

  function handleObservation(r) {
    const ojo = $("ojo");
    ojo.className = "ojo" + (r.n_faces > 1 ? " varias" : r.n_faces === 1 ? " ve" : "");
    drawFace(r);
    if (DEBUG) {
      const box = r.face_box ? `${Math.round(r.face_box[2] - r.face_box[0])}px` : "–";
      $("debug").textContent = `caras: ${r.n_faces} · tamaño: ${box} · candidato: ${r.candidate ? "sí" : "no"} · score: ${r.score ?? "–"} · sesión: ${r.session ? (r.session.nombre || "general") : "–"}`;
    }
    if (session) {
      if (!r.session) { goIdle(); return; }           // la persona se fue
      session = { ...session, ...r.session };
      $("privacy").classList.toggle("hidden", !session.privacy_mode);
      return;
    }
    if (r.started && r.session) { enterSession(r.session); return; }
    // Hay caras pero nadie reconocido: después de unos segundos, sugerir alternativas.
    if (r.n_faces > 0 && FACIAL) {
      facesSince = facesSince || Date.now();
      if (Date.now() - facesSince > 6000) $("idle-hint").classList.remove("hidden");
    } else {
      facesSince = null;
      $("idle-hint").classList.add("hidden");
    }
  }

  // ------------------------------------------------------------------ sesión
  async function enterSession(s, { quick } = {}) {
    session = s;
    facesSince = null;
    document.body.className = "texto-" + (s.tamano_texto || "grande");
    idle.classList.add("hidden"); sessionScreen.classList.remove("hidden");
    $("cards").innerHTML = ""; $("assistant-text").textContent = ""; $("user-text").classList.add("hidden");
    $("btn-visitas").classList.toggle("hidden", !s.resident_id);
    $("privacy").classList.toggle("hidden", !s.privacy_mode);
    setEstado("Un momento…", "pensando");
    try {
      const r = await postJSON(`/api/session/${s.id}/open`, {});
      renderHome(r.home);
      await showReply(r.reply);
      if (quick) await say(quick);
    } catch (e) {
      $("assistant-text").textContent = "Hola. En este momento no puedo hablar, pero podés usar los botones.";
      setEstado("");
    }
  }

  function goIdle() {
    stopAudio();
    session = null;
    document.body.className = "texto-grande";
    sessionScreen.classList.add("hidden"); idle.classList.remove("hidden");
    $("idle-hint").classList.add("hidden");
    facesSince = null;
  }

  async function endSession() {
    if (session) { try { await postJSON(`/api/session/${session.id}/end`, {}); } catch (_) { /* nada */ } }
    goIdle();
  }

  function renderHome(home) {
    $("saludo").textContent = home.saludo;
    const cards = $("cards");
    cards.innerHTML = "";
    if (home.privacy_mode) $("privacy").classList.remove("hidden");
    if (home.visita) cards.appendChild(visitaCard(home.visita));
    home.agenda.forEach(a => {
      const d = document.createElement("div");
      d.className = "card" + (a.en_curso ? " ahora" : "");
      d.innerHTML = `<span class="hora">${a.hora}</span><span class="titulo">${esc(a.titulo)}</span>` +
        (a.lugar ? `<span class="lugar">${esc(a.lugar)}</span>` : "") + (a.en_curso ? `<span class="meta">Ahora</span>` : "");
      cards.appendChild(d);
    });
    if (!home.agenda.length) {
      const d = document.createElement("div"); d.className = "card";
      d.innerHTML = `<span class="titulo">No queda nada en la agenda de hoy.</span>`; cards.appendChild(d);
    }
  }

  function visitaCard(v) {
    const d = document.createElement("div");
    d.className = "card visita";
    const quien = v.con_quien ? ` · ${esc(v.con_quien)}` : "";
    const estado = v.estado !== "confirmado" ? ` <span class="meta">(${esc(v.estado)})</span>` : "";
    d.innerHTML = `<div><span class="titulo">${esc(v.titulo)}${estado}</span><span class="lugar">${v.hora}${quien}${v.lugar ? " · " + esc(v.lugar) : ""}</span></div>` +
      `<div class="cuenta" data-min="${v.minutos}">${cuenta(v.minutos)}</div>`;
    return d;
  }
  function cuenta(min) {
    if (min <= 0 && min > -90) return "Es ahora";
    if (min <= -90) return "Ya pasó";
    if (min < 60) return `Faltan ${min} min`;
    const h = Math.floor(min / 60), m = min % 60;
    return `Falta${h > 1 ? "n" : ""} ${h} h${m >= 10 ? ` ${m} min` : ""}`;
  }
  setInterval(() => {
    document.querySelectorAll(".cuenta[data-min]").forEach(el => { el.dataset.min = +el.dataset.min - 1; el.textContent = cuenta(+el.dataset.min); });
  }, 60000);

  function renderCards(cardsData) {
    if (!cardsData || !cardsData.length) return;
    const cards = $("cards");
    cards.innerHTML = "";
    cardsData.forEach(c => {
      const d = document.createElement("div");
      d.className = "card " + c.type;
      if (c.type === "agenda" || c.type === "visitas") {
        d.className = "card menu";
        d.innerHTML = `<span class="titulo">${esc(c.titulo)}</span><ul>` + c.items.map(i =>
          `<li><b>${i.hora}</b> ${esc(i.titulo)}${i.fecha && i.fecha !== "hoy" ? " (" + esc(i.fecha) + ")" : ""}${i.lugar ? " · " + esc(i.lugar) : ""}${i.estado && i.estado !== "confirmado" ? " · " + esc(i.estado) : ""}${i.cuanto_falta ? " · " + esc(i.cuanto_falta) : ""}</li>`).join("") + "</ul>";
      } else if (c.type === "menu") {
        d.innerHTML = `<span class="titulo">${esc(c.titulo)}</span><ul>` + c.items.map(i =>
          `<li><b>${cap(i.servicio)}:</b> ${esc(i.plato)}${i.alternativa_habilitada_por_cocina ? `<br><span class="meta">Alternativa: ${esc(i.alternativa_habilitada_por_cocina)}</span>` : ""}</li>`).join("") + "</ul>";
      } else if (c.type === "ayuda") {
        d.innerHTML = `${esc(c.titulo)}<br><span class="meta">${esc(c.texto || "")}</span>`;
      } else {
        d.innerHTML = `<span class="titulo">${esc(c.titulo || "")}</span>${c.texto ? `<span class="meta">${esc(c.texto)}</span>` : ""}`;
      }
      cards.appendChild(d);
    });
  }

  // ------------------------------------------------------------------ conversación
  async function say(text, canal = "texto") {
    if (!session || busy) return;
    busy = true;
    stopAudio();
    $("user-text").textContent = text; $("user-text").classList.remove("hidden");
    setEstado("Pensando…", "pensando");
    try {
      const r = await postJSON(`/api/session/${session.id}/chat`, { text, canal });
      session = { ...session, ...r.session };
      $("privacy").classList.toggle("hidden", !session.privacy_mode);
      await showReply(r.reply);
    } catch (e) {
      $("assistant-text").textContent = e.status === 404 ? "La sesión terminó. Acercate de nuevo cuando quieras." : "Perdoná, no te pude responder. Probá de nuevo o tocá 'Necesito ayuda'.";
      setEstado("");
      if (e.status === 404) goIdle();
    } finally {
      busy = false;
    }
  }

  async function showReply(reply) {
    $("assistant-text").textContent = reply.text;
    renderCards(reply.cards);
    await speak(reply.text);
  }

  async function speak(text) {
    if (!VOZ || !text) { setEstado(""); return; }
    setEstado("Hablando…", "hablando");
    try {
      const res = await api("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, session_id: session && session.id }) });
      const url = URL.createObjectURL(await res.blob());
      stopAudio();
      audioEl = new Audio(url);
      audioEl.onended = () => { setEstado(""); URL.revokeObjectURL(url); };
      await audioEl.play();
    } catch (e) {
      console.warn("tts:", e.message); setEstado("");
    }
  }
  function stopAudio() { if (audioEl) { audioEl.pause(); audioEl = null; } setEstado(""); }

  // ------------------------------------------------------------------ micrófono → WAV → ASR
  async function toggleMic() {
    if (!VOZ) return;
    if (recorder && recorder.state === "recording") { recorder.stop(); return; }
    stopAudio();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        $("mic").classList.remove("grabando"); $("mic").querySelector(".mic-label").textContent = "Tocá para hablar";
        setEstado("Entendiendo…", "pensando");
        try {
          const wav = await toWav(new Blob(chunks, { type: recorder.mimeType }));
          const fd = new FormData(); fd.append("audio", wav, "voz.wav");
          const { text } = await (await api("/api/asr", { method: "POST", body: fd })).json();
          if (text) await say(text, "voz"); else { $("assistant-text").textContent = "No te escuché bien. ¿Me lo repetís?"; setEstado(""); }
        } catch (e) { console.warn("asr:", e.message); setEstado(""); $("assistant-text").textContent = "No pude escucharte. Probá de nuevo o escribí abajo."; }
      };
      recorder.start();
      $("mic").classList.add("grabando"); $("mic").querySelector(".mic-label").textContent = "Te escucho… tocá al terminar";
      setEstado("Escuchando", "escuchando");
      setTimeout(() => { if (recorder && recorder.state === "recording") recorder.stop(); }, 15000);
    } catch (e) { console.warn("mic:", e); setEstado(""); }
  }

  async function toWav(blob) {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const buf = await ac.decodeAudioData(await blob.arrayBuffer());
    const rate = 16000, len = Math.ceil(buf.duration * rate);
    const oc = new OfflineAudioContext(1, len, rate);
    const src = oc.createBufferSource(); src.buffer = buf; src.connect(oc.destination); src.start();
    const mono = (await oc.startRendering()).getChannelData(0);
    const out = new DataView(new ArrayBuffer(44 + mono.length * 2));
    const w = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); out.setUint32(4, 36 + mono.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
    out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
    out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
    w(36, "data"); out.setUint32(40, mono.length * 2, true);
    for (let i = 0; i < mono.length; i++) { const s = Math.max(-1, Math.min(1, mono[i])); out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
    ac.close();
    return new Blob([out], { type: "audio/wav" });
  }

  // ------------------------------------------------------------------ acciones
  async function startGeneral(motivo, quick) {
    const r = await postJSON("/api/session/start", { kiosk_id: KIOSK_ID, mode: "general", motivo });
    await enterSession(r.session, { quick });
  }

  async function help() {
    try {
      if (!session) { const r = await postJSON("/api/session/start", { kiosk_id: KIOSK_ID, mode: "general", motivo: "eleccion" }); session = r.session; idle.classList.add("hidden"); sessionScreen.classList.remove("hidden"); $("saludo").textContent = "Ya avisamos al equipo"; }
      const r = await postJSON(`/api/session/${session.id}/help`, {});
      renderCards([r.card]);
      $("assistant-text").textContent = "Ya avisé a recepción. En un momento viene alguien a ayudarte. Quedate tranquila o tranquilo.";
      await speak($("assistant-text").textContent);
    } catch (e) { console.warn("help:", e.message); }
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const a = btn.dataset.action;
    if (btn.dataset.say) return say(btn.dataset.say);
    if (a === "general-agenda") return startGeneral(FACIAL && facesSince ? "no_reconocido" : "eleccion");
    if (a === "general-menu") return startGeneral(FACIAL && facesSince ? "no_reconocido" : "eleccion", "¿Qué comemos hoy?");
    if (a === "pin") { $("pin-error").classList.add("hidden"); $("pin-input").value = ""; $("pin-dialog").showModal(); return; }
    if (a === "pin-cancel") return $("pin-dialog").close();
    if (a === "help") return help();
    if (a === "end") return endSession();
  });

  $("pin-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const rid = (document.querySelector('input[name="resident"]:checked') || {}).value;
    const pin = $("pin-input").value.trim();
    if (!rid || !pin) { $("pin-error").textContent = "Elegí tu nombre y escribí tu PIN."; $("pin-error").classList.remove("hidden"); return; }
    try {
      const r = await postJSON("/api/session/start", { kiosk_id: KIOSK_ID, mode: "pin", resident_id: rid, pin });
      $("pin-dialog").close();
      await enterSession(r.session);
    } catch (e) {
      $("pin-error").textContent = "Ese PIN no coincide. Probá de nuevo o pedí ayuda.";
      $("pin-error").classList.remove("hidden");
    }
  });

  $("text-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const t = $("text-input").value.trim();
    if (!t) return;
    $("text-input").value = "";
    say(t, "texto");
  });
  $("mic").addEventListener("click", toggleMic);
  if (!VOZ) $("mic").classList.add("hidden");

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : ""; }

  startCamera();
})();
