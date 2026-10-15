// wq#4031 kiosk chat UI client.
// Written against Android 7.1.2 WebView + modern Chromium both: plain <script>,
// no ES modules, no optional chaining/nullish coalescing, feature-detect every
// browser API (getUserMedia, AudioContext) instead of assuming support.
(function () {
  'use strict';

  var VOICE_WS_PATH = '/ws/voice';
  var SEND_SAMPLE_RATE = 16000;

  var logEl = document.getElementById('log');
  var form = document.getElementById('chat-form');
  var input = document.getElementById('chat-input');
  var voiceBtn = document.getElementById('voice-btn');
  var cameraBtn = document.getElementById('camera-btn');
  var cameraVideo = document.getElementById('camera');
  var snapshotCanvas = document.getElementById('snapshot-canvas');

  var AudioContextCtor = window.AudioContext || window.webkitAudioContext;

  function appendLine(role, text) {
    var line = document.createElement('div');
    line.className = 'line ' + role;
    line.textContent = role + ': ' + text;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  // ---- text chat -------------------------------------------------------
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var message = input.value.trim();
    if (!message) return;
    appendLine('you', message);
    input.value = '';
    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: message })
    }).then(function (resp) {
      return resp.json();
    }).then(function (data) {
      appendLine('lumen', data.reply);
      playTtsUrl('/api/tts?text=' + encodeURIComponent(data.reply));
    }).catch(function (err) {
      appendLine('error', String(err));
    });
  });

  // ---- TTS playback via Web Audio API -----------------------------------
  var ttsAudioCtx = null;

  function getTtsAudioCtx() {
    if (!ttsAudioCtx && AudioContextCtor) ttsAudioCtx = new AudioContextCtor();
    return ttsAudioCtx;
  }

  function playWavArrayBuffer(arrayBuffer) {
    var ctx = getTtsAudioCtx();
    if (!ctx) return;
    ctx.decodeAudioData(arrayBuffer, function (audioBuffer) {
      var source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);
      source.start(0);
    }, function (err) {
      appendLine('error', 'tts decode failed: ' + err);
    });
  }

  function playTtsUrl(url) {
    fetch(url).then(function (resp) {
      if (!resp.ok) throw new Error('tts http ' + resp.status);
      return resp.arrayBuffer();
    }).then(playWavArrayBuffer).catch(function (err) {
      appendLine('error', String(err));
    });
  }

  // ---- voice: WebSocket PCM16 stream to /ws/voice ------------------------
  var voiceState = { active: false, ws: null, audioCtx: null, processor: null, source: null, stream: null };

  function downsampleTo16k(float32Samples, inputSampleRate) {
    if (inputSampleRate === SEND_SAMPLE_RATE) return float32Samples;
    var ratio = inputSampleRate / SEND_SAMPLE_RATE;
    var outLength = Math.floor(float32Samples.length / ratio);
    var out = new Float32Array(outLength);
    for (var i = 0; i < outLength; i++) {
      out[i] = float32Samples[Math.floor(i * ratio)];
    }
    return out;
  }

  function floatToPcm16(float32Samples) {
    var out = new Int16Array(float32Samples.length);
    for (var i = 0; i < float32Samples.length; i++) {
      var s = Math.max(-1, Math.min(1, float32Samples[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function startVoice() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      appendLine('error', 'microphone not supported in this browser');
      return;
    }
    if (!AudioContextCtor) {
      appendLine('error', 'Web Audio API not supported in this browser');
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + VOICE_WS_PATH);
      ws.binaryType = 'arraybuffer';

      ws.onmessage = function (ev) {
        if (typeof ev.data === 'string') {
          var msg = JSON.parse(ev.data);
          if (msg.type === 'transcript') appendLine('you (voice)', msg.text);
          else if (msg.type === 'reply') appendLine('lumen', msg.text);
          else if (msg.type === 'error') appendLine('error', msg.detail);
        } else {
          playWavArrayBuffer(ev.data);
        }
      };
      ws.onerror = function () { appendLine('error', 'voice websocket error'); };

      var audioCtx = new AudioContextCtor();
      var source = audioCtx.createMediaStreamSource(stream);
      // ScriptProcessorNode is deprecated but is the one capture API that works
      // across both Android 7.1.2 WebView and modern Chromium without an
      // AudioWorklet module (AudioWorklet needs ES module support WebView lacks
      // on older builds).
      var processor = audioCtx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = function (ev) {
        if (ws.readyState !== WebSocket.OPEN) return;
        var input16k = downsampleTo16k(ev.inputBuffer.getChannelData(0), audioCtx.sampleRate);
        var pcm16 = floatToPcm16(input16k);
        ws.send(pcm16.buffer);
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);

      voiceState = { active: true, ws: ws, audioCtx: audioCtx, processor: processor, source: source, stream: stream };
      voiceBtn.textContent = '⏹ Stop';
    }).catch(function (err) {
      appendLine('error', 'mic access denied: ' + err);
    });
  }

  function stopVoice() {
    if (voiceState.processor) voiceState.processor.disconnect();
    if (voiceState.source) voiceState.source.disconnect();
    if (voiceState.stream) {
      var tracks = voiceState.stream.getTracks();
      for (var i = 0; i < tracks.length; i++) tracks[i].stop();
    }
    if (voiceState.ws) voiceState.ws.close();
    if (voiceState.audioCtx) voiceState.audioCtx.close();
    voiceState = { active: false, ws: null, audioCtx: null, processor: null, source: null, stream: null };
    voiceBtn.textContent = '🎤 Voice';
  }

  voiceBtn.addEventListener('click', function () {
    if (voiceState.active) stopVoice();
    else startVoice();
  });

  // ---- camera snapshot ---------------------------------------------------
  var cameraStream = null;

  function ensureCameraStream() {
    if (cameraStream) return Promise.resolve(cameraStream);
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error('camera not supported in this browser'));
    }
    return navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }).then(function (stream) {
      cameraStream = stream;
      cameraVideo.srcObject = stream;
      return stream;
    });
  }

  cameraBtn.addEventListener('click', function () {
    ensureCameraStream().then(function () {
      return new Promise(function (resolve) {
        if (cameraVideo.readyState >= 2) resolve();
        else cameraVideo.onloadeddata = function () { resolve(); };
      });
    }).then(function () {
      snapshotCanvas.width = cameraVideo.videoWidth || 640;
      snapshotCanvas.height = cameraVideo.videoHeight || 480;
      var ctx2d = snapshotCanvas.getContext('2d');
      ctx2d.drawImage(cameraVideo, 0, 0, snapshotCanvas.width, snapshotCanvas.height);
      snapshotCanvas.toBlob(function (blob) {
        var formData = new FormData();
        formData.append('file', blob, 'snapshot.jpg');
        fetch('/api/camera/snapshot', { method: 'POST', body: formData }).then(function (resp) {
          return resp.json();
        }).then(function (data) {
          appendLine('system', 'snapshot saved: ' + data.path);
        });
      }, 'image/jpeg', 0.85);
    }).catch(function (err) {
      appendLine('error', 'camera access denied: ' + err);
    });
  });
})();
