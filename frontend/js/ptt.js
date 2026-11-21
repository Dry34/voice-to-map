import { currentLang } from "./prefs.js";

const tr = (fr, en) => (currentLang() === "en" ? en : fr);

// Push-to-talk : maintenir le bouton (ou la barre espace) pour parler, relâcher pour envoyer.
// Responsable : A.

const MIN_MS = 400; // en dessous, on considère que c'est un clic accidentel

export function setupPushToTalk({ button, upload, onStatus, onEvent }) {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    button.disabled = true;
    onStatus("err", tr("Ce navigateur ne permet pas d'enregistrer le micro. Utilisez Chrome ou Safari.", "This browser cannot record the microphone. Use Chrome or Safari."));
    return;
  }

  let stream = null;
  let recorder = null;
  let chunks = [];
  let startedAt = 0;
  let busy = false;

  async function start() {
    if (recorder || busy || button.disabled || button.closest("[hidden]")) return;
    try {
      stream = stream || (await navigator.mediaDevices.getUserMedia({ audio: true }));
    } catch {
      onStatus("err", tr("Accès au micro refusé. Autorisez-le dans les réglages du navigateur.", "Microphone access denied. Allow it in the browser settings."));
      return;
    }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = send;
    recorder.start();
    startedAt = performance.now();
    button.classList.add("recording");
    button.textContent = tr("● Enregistrement… relâchez pour envoyer", "● Recording… release to send");
    onStatus("", tr("Parlez…", "Speak…"));
  }

  function stop() {
    if (!recorder) return;
    recorder.stop();
    recorder = null;
    button.classList.remove("recording");
    button.textContent = tr("Maintenir pour parler", "Hold to talk");
  }

  async function send() {
    const duration = performance.now() - startedAt;
    if (duration < MIN_MS) {
      onStatus("", tr("Trop court : maintenez le bouton pendant que vous parlez.", "Too short: hold the button while you speak."));
      return;
    }
    const type = chunks[0]?.type || "audio/webm";
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";

    busy = true;
    button.disabled = true;
    onStatus("", tr("Transcription et analyse…", "Transcribing and analysing…"));
    try {
      onEvent(await upload(new Blob(chunks, { type }), `annonce.${ext}`));
    } catch (err) {
      onStatus("err", `${tr("Échec", "Failed")}: ${err.message}`);
    } finally {
      busy = false;
      button.disabled = false;
    }
  }

  // Souris, doigt et stylet
  button.addEventListener("pointerdown", (e) => { e.preventDefault(); button.setPointerCapture(e.pointerId); start(); });
  button.addEventListener("pointerup", stop);
  button.addEventListener("pointercancel", stop);

  // Barre espace, sauf quand on tape dans un champ
  const typing = () => ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.repeat && !typing()) { e.preventDefault(); start(); }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && !typing()) { e.preventDefault(); stop(); }
  });
}
