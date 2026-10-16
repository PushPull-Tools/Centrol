// Pont minimal entre la page de configuration et le processus principal.
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('centrol', {desktop: true, setUrl: (u) => ipcRenderer.send('centrol:set-url', String(u))});
