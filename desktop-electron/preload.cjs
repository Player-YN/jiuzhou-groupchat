'use strict';

/**
 * Preload — expose minimal desktop bridge for frameless ink-gold title bar.
 * contextIsolation: true, nodeIntegration: false
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jiuzhouDesktop', {
  isDesktop: true,
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximize: () => ipcRenderer.invoke('window-maximize'),
  close: () => ipcRenderer.invoke('window-close'),
  isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
  onMaximizeChange: (cb) => {
    if (typeof cb !== 'function') return () => {};
    const handler = (_e, value) => cb(!!value);
    ipcRenderer.on('window-maximize-changed', handler);
    return () => ipcRenderer.removeListener('window-maximize-changed', handler);
  },
});
