import { TNY360 } from '@tny-robotics/sdk';

const ROBOT_IP = '192.168.1.30';
const SERVER_URL = 'http://192.168.1.188:5000';

// IMPORTANT : le navigateur ne peut pas lancer child_process directement.
// Ces routes doivent donc être servies par le backend Node qui héberge
// l'application. Elles lancent/arrêtent le serveur Python.
const PYTHON_START_URL = '/api/follow/start';
const PYTHON_STOP_URL = '/api/follow/stop';

const FRAME_WIDTH = 640;
const FORWARD_SPEED = 0.25;
const MAX_ROTATION_SPEED = 1.0;
const DEAD_ZONE = 70;
const FOLLOW_LOOP_INTERVAL_MS = 1000;

const voltageText = document.getElementById('val-voltage') as HTMLSpanElement;
const currentText = document.getElementById('val-current') as HTMLSpanElement;
const powerText = document.getElementById('val-power') as HTMLSpanElement;
const btnBody = document.getElementById('btn-toggle-body') as HTMLButtonElement;
const btnFollow = document.getElementById('btn-follow') as HTMLButtonElement;
const followLabel = document.getElementById('follow-label') as HTMLSpanElement;
const followStatus = document.getElementById('follow-status') as HTMLDivElement;
const btnUp = document.getElementById('btn-up') as HTMLButtonElement;
const btnDown = document.getElementById('btn-down') as HTMLButtonElement;
const btnLeft = document.getElementById('btn-left') as HTMLButtonElement;
const btnRight = document.getElementById('btn-right') as HTMLButtonElement;
const btnRotateLeft = document.getElementById('btn-rotate-left') as HTMLButtonElement;
const btnRotateRight = document.getElementById('btn-rotate-right') as HTMLButtonElement;
const videoStream = document.getElementById('video-stream') as HTMLImageElement;
const videoFallback = document.getElementById('video-fallback') as HTMLDivElement;

const robot = new TNY360(ROBOT_IP);
let isBodyActive = true;
let isConnected = false;
let isConnecting = false;
let isFollowActive = false;
let isFollowStarting = false;
let followLoopRunning = false;
let followGeneration = 0;

function setFollowStatus(text: string, state: 'inactive'|'starting'|'active'|'error') {
  followStatus.textContent = text;
  followStatus.className = `follow-status ${state}`;
}

function setManualButtonsEnabled(enabled: boolean) {
  [btnUp,btnDown,btnLeft,btnRight,btnRotateLeft,btnRotateRight].forEach(b => b.disabled = !enabled);
}

async function stopRobot() {
  if (!isConnected) return;
  try { await robot.body.setVelocity(0,0,0); }
  catch (e) { console.error('Erreur arrêt robot :', e); }
}

async function connectRobot(): Promise<boolean> {
  if (isConnected) return true;
  if (isConnecting) return false;
  isConnecting = true;
  try {
    await robot.connect();
    isConnected = true;
    console.log('✅ Robot connecté.');
    updateBodyButton();
    return true;
  } catch (e) {
    isConnected = false;
    console.error('❌ Connexion robot impossible :', e);
    return false;
  } finally { isConnecting = false; }
}

function updateBodyButton() {
  btnBody.textContent = isBodyActive ? 'BODY : ON' : 'BODY : OFF';
  btnBody.className = isBodyActive ? 'btn-body active' : 'btn-body inactive';
}

btnBody.addEventListener('click', async () => {
  if (!isConnected || btnBody.disabled) return;
  btnBody.disabled = true;
  try {
    if (isBodyActive) {
      console.log('BODY déjà ON -> aucune commande.');
      return;
    }
    isBodyActive = true;
    updateBodyButton();
    await robot.body.enableSmooth();
  } catch (e) {
    console.error('Erreur activation BODY :', e);
    isBodyActive = false;
    updateBodyButton();
  } finally { btnBody.disabled = false; }
});

// Le backend doit attendre lui-même que Python soit prêt et renvoyer
// { ready: true }. Le fallback ci-dessous vérifie aussi l'endpoint YOLO.
async function startPythonServer(): Promise<boolean> {
  try {
    const response = await fetch(PYTHON_START_URL, { method:'POST', headers:{'Content-Type':'application/json'} });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json().catch(() => ({}));
    if (result.ready === true) return true;
    return await waitForPythonServer();
  } catch (e) {
    console.error('❌ Impossible de démarrer Python :', e);
    return false;
  }
}

async function waitForPythonServer(timeoutMs = 30000): Promise<boolean> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!isFollowStarting && !isFollowActive) return false;
    try {
      const response = await fetch(`${SERVER_URL}/coords/primary`, { cache:'no-store' });
      if (response.ok) return true;
    } catch {}
    await new Promise(r => setTimeout(r,500));
  }
  return false;
}

async function stopPythonServer() {
  try {
    const response = await fetch(PYTHON_STOP_URL, { method:'POST', headers:{'Content-Type':'application/json'} });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    console.log('🐍 Serveur Python arrêté.');
  } catch (e) { console.error('⚠️ Impossible d’arrêter Python :', e); }
}

async function getPersonCenter(): Promise<[number,number]|null> {
  try {
    const response = await fetch(`${SERVER_URL}/coords/primary`, { cache:'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.primary || !Array.isArray(data.primary.center)) return null;
    return data.primary.center as [number,number];
  } catch (e) {
    console.error('Erreur serveur YOLO :', e);
    return null;
  }
}

function calculateRotation(cx: number): number {
  const error = cx - FRAME_WIDTH / 2;
  if (Math.abs(error) <= DEAD_ZONE) return 0;
  const normalized = error / Math.max(1, FRAME_WIDTH/2 - DEAD_ZONE);
  return -Math.max(-1,Math.min(1,normalized)) * MAX_ROTATION_SPEED;
}

async function followPersonLoop(generation: number) {
  if (followLoopRunning) return;
  followLoopRunning = true;
  try {
    while (isFollowActive && generation === followGeneration) {
      if (!isConnected || !isBodyActive) { await stopRobot(); break; }
      const center = await getPersonCenter();
      if (!isFollowActive || generation !== followGeneration) break;
      if (!center) {
        console.log('👤 Aucune personne détectée -> STOP');
        await stopRobot();
      } else {
        const rotation = calculateRotation(center[0]);
        try { await robot.body.setVelocity(FORWARD_SPEED,0,rotation); }
        catch (e) { console.error('Erreur commande suivi :',e); isConnected=false; break; }
      }
      await new Promise(r => setTimeout(r,FOLLOW_LOOP_INTERVAL_MS));
    }
  } finally {
    followLoopRunning = false;
    if (generation === followGeneration) await stopRobot();
  }
}

async function enableFollowMode() {
  if (isFollowActive || isFollowStarting || !isConnected) return;
  if (!isBodyActive) { setFollowStatus('Active d’abord le BODY','error'); return; }

  isFollowStarting = true;
  btnFollow.disabled = true;
  setManualButtonsEnabled(false);
  setFollowStatus('Démarrage du serveur Python…','starting');

  const ready = await startPythonServer();
  if (!ready || !isFollowStarting) {
    if (isFollowStarting) await stopPythonServer();
    isFollowStarting = false;
    btnFollow.disabled = false;
    setManualButtonsEnabled(true);
    setFollowStatus('Impossible de démarrer YOLO','error');
    return;
  }

  isFollowStarting = false;
  isFollowActive = true;
  followGeneration++;
  btnFollow.disabled = false;
  btnFollow.className = 'btn-follow active';
  followLabel.textContent = 'Arrêter le suivi';
  setFollowStatus('Suivi actif • YOLO prêt','active');
  void followPersonLoop(followGeneration);
}

async function disableFollowMode() {
  if (!isFollowActive && !isFollowStarting) return;
  isFollowActive = false;
  isFollowStarting = false;
  followGeneration++;
  btnFollow.disabled = true;
  btnFollow.className = 'btn-follow inactive';
  followLabel.textContent = 'Suivi d’une personne';
  setFollowStatus('Arrêt du suivi…','starting');
  await stopRobot();
  await stopPythonServer();
  btnFollow.disabled = false;
  setManualButtonsEnabled(true);
  setFollowStatus('Suivi désactivé','inactive');
}

btnFollow.addEventListener('click', async () => {
  if (isFollowActive || isFollowStarting) await disableFollowMode();
  else await enableFollowMode();
});

async function manualVelocity(vx:number,vy:number,vz:number) {
  if (isFollowActive || isFollowStarting || !isConnected || !isBodyActive) return;
  try { await robot.body.setVelocity(vx,vy,vz); }
  catch (e) { console.error('Erreur mouvement manuel :',e); }
}

async function manualStop() {
  if (isFollowActive || isFollowStarting) return;
  await stopRobot();
}

function configureMovementButton(button:HTMLButtonElement, action:()=>Promise<void>) {
  button.addEventListener('pointerdown', async e => {
    e.preventDefault(); if (button.disabled) return;
    try { button.setPointerCapture(e.pointerId); } catch {}
    await action();
  });
  const stop = async (e:Event) => { e.preventDefault(); await manualStop(); };
  button.addEventListener('pointerup',stop);
  button.addEventListener('pointercancel',stop);
  button.addEventListener('lostpointercapture',async()=>await manualStop());
}

configureMovementButton(btnUp,()=>manualVelocity(.25,0,0));
configureMovementButton(btnDown,()=>manualVelocity(-.25,0,0));
configureMovementButton(btnLeft,()=>manualVelocity(0,.25,0));
configureMovementButton(btnRight,()=>manualVelocity(0,-.25,0));
configureMovementButton(btnRotateLeft,()=>manualVelocity(0,0,1));
configureMovementButton(btnRotateRight,()=>manualVelocity(0,0,-1));

async function updateTelemetry() {
  if (!isConnected) return;
  try {
    voltageText.textContent=(await robot.power.getVoltage()).toFixed(2);
    currentText.textContent=(await robot.power.getCurrent()).toFixed(2);
    powerText.textContent=(await robot.power.getPower()).toFixed(2);
  } catch(e) { console.error('Erreur télémétrie :',e); }
}

videoStream.addEventListener('load',()=>{videoStream.style.display='block';videoFallback.style.display='none';});
videoStream.addEventListener('error',()=>{videoStream.style.display='none';videoFallback.style.display='flex';});

window.addEventListener('beforeunload',()=>{
  if (isFollowActive || isFollowStarting) {
    try { navigator.sendBeacon(PYTHON_STOP_URL,new Blob([JSON.stringify({reason:'page_closed'})],{type:'application/json'})); } catch {}
  }
});

async function demarrer() {
  updateBodyButton();
  setManualButtonsEnabled(true);
  setFollowStatus('Suivi désactivé','inactive');
  if (!await connectRobot()) { setFollowStatus('Robot non connecté','error'); return; }
  await updateTelemetry();
  setInterval(()=>void updateTelemetry(),1000);
}

window.addEventListener('DOMContentLoaded',()=>void demarrer());
