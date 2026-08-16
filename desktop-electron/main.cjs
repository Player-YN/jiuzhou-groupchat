'use strict';

/**
 * 九洲一号群 — Electron 壳
 *
 * 推荐由 start-electron.bat 两阶段启动：
 *   1) groupchat-lifecycle.ps1 先起 8000/3000
 *   2) electron . --no-spawn  只开窗
 * 关窗 / before-quit：始终 stop 后端+前端（不依赖 stop.bat）
 *
 * 也支持 electron .  （自行起服务）
 */

const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { spawn, execFileSync } = require('child_process');

// Larger game-like window (plan: 1440×900, min 1280×800, resizable)
const WINDOW_WIDTH = 1440;
const WINDOW_HEIGHT = 900;
const WINDOW_MIN_WIDTH = 1280;
const WINDOW_MIN_HEIGHT = 800;
const BACKEND_PORT = 8000;
const FRONTEND_PORT = 3000;
const READY_TIMEOUT_MS = 120000;
const READY_POLL_MS = 500;

const repoRoot = path.join(__dirname, '..');
const backendDir = path.join(repoRoot, 'backend');
const frontendDir = path.join(repoRoot, 'frontend');
const lifecyclePs1 = path.join(repoRoot, 'scripts', 'groupchat-lifecycle.ps1');
const lockFile = path.join(backendDir, '.groupchat.lock');
const backendPidPath = path.join(backendDir, '.uvicorn.pid');
const frontendPidPath = path.join(frontendDir, '.next_server.pid');
const electronLogPath = path.join(__dirname, 'electron.log');

/** @type {BrowserWindow | null} */
let mainWindow = null;
let cleaningUp = false;
/** When true, this process started services and must stop them on quit. */
let ownsServices = false;

function hasFlag(name) {
  return process.argv.includes(name);
}

function log(...args) {
  const line = `[electron ${new Date().toISOString()}] ${args.map(String).join(' ')}`;
  console.log(line);
  try {
    fs.appendFileSync(electronLogPath, line + '\n', 'utf8');
  } catch {
    /* ignore */
  }
}

function readPidFile(filePath) {
  try {
    if (!fs.existsSync(filePath)) return null;
    const raw = fs.readFileSync(filePath, 'utf8').trim();
    if (/^\d+$/.test(raw)) return parseInt(raw, 10);
  } catch {
    /* ignore */
  }
  return null;
}

function killPidTree(pid) {
  if (!pid || !Number.isFinite(pid) || pid <= 0) return;
  if (pid === process.pid) return;
  try {
    execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], {
      stdio: 'ignore',
      windowsHide: true,
    });
    log('killed PID tree', pid);
  } catch {
    /* already dead */
  }
}

function waitForPort(port, host = '127.0.0.1', timeoutMs = READY_TIMEOUT_MS) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = new net.Socket();
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        try {
          socket.destroy();
        } catch {
          /* ignore */
        }
        if (ok) resolve();
        else if (Date.now() - started >= timeoutMs) {
          reject(new Error(`Timed out waiting for ${host}:${port}`));
        } else setTimeout(attempt, READY_POLL_MS);
      };
      socket.setTimeout(1000);
      socket.once('connect', () => finish(true));
      socket.once('timeout', () => finish(false));
      socket.once('error', () => finish(false));
      try {
        socket.connect(port, host);
      } catch {
        finish(false);
      }
    };
    attempt();
  });
}

function waitForHttp(url, timeoutMs = READY_TIMEOUT_MS) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(url, { timeout: 2500 }, (res) => {
        res.resume();
        const code = res.statusCode || 0;
        if (code >= 200 && code < 500) resolve();
        else if (Date.now() - started >= timeoutMs) {
          reject(new Error(`HTTP ${code} from ${url}`));
        } else setTimeout(attempt, READY_POLL_MS);
      });
      req.on('error', () => {
        if (Date.now() - started >= timeoutMs) {
          reject(new Error(`Timed out waiting for ${url}`));
        } else setTimeout(attempt, READY_POLL_MS);
      });
      req.on('timeout', () => req.destroy());
    };
    attempt();
  });
}

function runLifecycle(args) {
  return new Promise((resolve, reject) => {
    log('lifecycle', args.join(' '));
    const child = spawn(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', lifecyclePs1, ...args],
      {
        cwd: repoRoot,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      }
    );
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (b) => {
      const s = b.toString();
      stdout += s;
      process.stdout.write(s);
    });
    child.stderr.on('data', (b) => {
      const s = b.toString();
      stderr += s;
      process.stderr.write(s);
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`lifecycle exit ${code}\n${stderr || stdout}`));
    });
  });
}

function writeElectronLock() {
  const payload = {
    mode: 'electron',
    holderPid: process.pid,
    backendPort: BACKEND_PORT,
    frontendPort: FRONTEND_PORT,
    startedAt: new Date().toISOString(),
    root: repoRoot,
  };
  try {
    fs.mkdirSync(backendDir, { recursive: true });
    fs.writeFileSync(lockFile, JSON.stringify(payload, null, 2), 'utf8');
  } catch (err) {
    log('lock write failed', err.message);
  }
}

function registerWindowIpc() {
  ipcMain.handle('window-minimize', () => {
    if (mainWindow) mainWindow.minimize();
  });
  ipcMain.handle('window-maximize', () => {
    if (!mainWindow) return false;
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
    return mainWindow.isMaximized();
  });
  ipcMain.handle('window-close', () => {
    if (mainWindow) mainWindow.close();
  });
  ipcMain.handle('window-is-maximized', () => {
    return !!(mainWindow && mainWindow.isMaximized());
  });
}

function createWindow(frontendUrl) {
  log('createWindow', frontendUrl);

  mainWindow = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    minWidth: WINDOW_MIN_WIDTH,
    minHeight: WINDOW_MIN_HEIGHT,
    resizable: true,
    maximizable: true,
    // Frameless → frontend DesktopTitleBar (墨金)
    frame: false,
    title: '九洲一号群',
    backgroundColor: '#1F1F1F',
    autoHideMenuBar: true,
    show: true,
    center: true,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.show();
  mainWindow.focus();

  const emitMax = () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(
        'window-maximize-changed',
        mainWindow.isMaximized()
      );
    }
  };
  mainWindow.on('maximize', emitMax);
  mainWindow.on('unmaximize', emitMax);

  mainWindow.webContents.on('did-fail-load', (_e, code, desc, failedUrl) => {
    log('did-fail-load', code, desc, failedUrl);
    dialog.showErrorBox(
      '九洲一号群 页面加载失败',
      `无法打开 ${failedUrl}\n\n${desc} (code ${code})\n\n请确认 3000 端口服务已启动。`
    );
  });
  mainWindow.webContents.on('did-finish-load', () => {
    log('did-finish-load');
    if (mainWindow) {
      mainWindow.setAlwaysOnTop(false);
      mainWindow.focus();
      emitMax();
    }
  });

  mainWindow.loadURL(frontendUrl).catch((err) => {
    log('loadURL rejected', err && err.message);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  log('window created, handle=', mainWindow.getNativeWindowHandle && 'ok');
}

/** Always free :8000/:3000 when the app closes (no separate stop.bat). */
function stopServicesOnQuit() {
  if (cleaningUp) return;
  cleaningUp = true;
  log('cleanup: stop backend/frontend on app close');
  try {
    execFileSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        lifecyclePs1,
        '-Action',
        'stop',
        '-SkipElectron',
        '-Quiet',
      ],
      { cwd: repoRoot, windowsHide: true, stdio: 'ignore' }
    );
  } catch (err) {
    log('lifecycle stop error', err.message || err);
    const be = readPidFile(backendPidPath);
    const fe = readPidFile(frontendPidPath);
    if (be) killPidTree(be);
    if (fe) killPidTree(fe);
    // last resort: free fixed ports
    try {
      execFileSync(
        'powershell.exe',
        [
          '-NoProfile',
          '-Command',
          `foreach($p in 8000,3000){ Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue } }`,
        ],
        { windowsHide: true, stdio: 'ignore' }
      );
    } catch {
      /* ignore */
    }
  }
  ownsServices = false;
  cleaningUp = false;
}

async function bootstrap() {
  try {
    try {
      fs.writeFileSync(electronLogPath, '', 'utf8');
    } catch {
      /* ignore */
    }

    const noSpawn = hasFlag('--no-spawn');
    const feUrl =
      process.env.FRONTEND_URL || `http://127.0.0.1:${FRONTEND_PORT}`;

    log('bootstrap begin noSpawn=', noSpawn, 'repoRoot=', repoRoot);

    if (noSpawn) {
      // Bat already started services — only open the window.
      ownsServices = false;
      writeElectronLock();
      log('wait TCP', FRONTEND_PORT);
      await waitForPort(FRONTEND_PORT, '127.0.0.1', 30000);
      log('wait HTTP', feUrl);
      await waitForHttp(feUrl, 30000);
      createWindow(feUrl);
      return;
    }

    // Self-contained mode: electron starts services then window
    writeElectronLock();
    await runLifecycle([
      '-Action',
      'start',
      '-Mode',
      'electron',
      '-AssumeLocked',
    ]);
    ownsServices = true;
    log('wait TCP', FRONTEND_PORT);
    await waitForPort(FRONTEND_PORT, '127.0.0.1');
    log('wait HTTP', feUrl);
    await waitForHttp(feUrl);
    createWindow(feUrl);
  } catch (err) {
    const msg = err && err.stack ? err.stack : String(err);
    log('Startup failed', msg);
    try {
      if (app.isReady()) {
        dialog.showErrorBox(
          '九洲一号群 启动失败',
          String((err && err.message) || err) +
            '\n\n请关闭已有窗口后重试 start-electron.bat。\n日志: desktop-electron\\electron.log'
        );
      }
    } catch {
      /* ignore */
    }
    stopServicesOnQuit();
    app.exit(1);
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  log('second instance — exit');
  // Don't kill services of the first instance
  app.exit(0);
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.setAlwaysOnTop(true);
      mainWindow.show();
      mainWindow.focus();
      mainWindow.setAlwaysOnTop(false);
    }
  });

  app.whenReady().then(() => {
    registerWindowIpc();
    return bootstrap();
  });

  app.on('window-all-closed', () => {
    stopServicesOnQuit();
    app.quit();
  });

  app.on('before-quit', () => {
    stopServicesOnQuit();
  });
}
