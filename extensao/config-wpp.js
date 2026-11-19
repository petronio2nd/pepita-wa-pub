// Lido pelo wa-js ao carregar: nada de telemetria.
// SEG-01: `linkPreviewApiServers: []` impede o wa-js de mandar os links colados no WhatsApp Web a servidores
// de terceiros (`[]` e verdadeiro em JS, entao o `||` do wa-js nao volta a lista padrao; a previa fica com
// o proprio WhatsApp). Esta opcao tem de sobreviver a toda troca de versao do wa-js (ver HANDOFF).
window.WPPConfig = { disableGoogleAnalytics: true, deviceName: false, linkPreviewApiServers: [] };
