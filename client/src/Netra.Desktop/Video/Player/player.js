// Bridge between WPF (LecturePlayerController) and the YouTube IFrame API.
// Messages in: load, play, pause, seek, seekBy, pauseAndReport, time.
// Messages out: apiReady, apiFailed, ready, state, error, time.
// Times are whole milliseconds read from the player itself.
(function () {
  'use strict';
  var host = window.chrome && window.chrome.webview;
  if (!host) { return; }

  var STATES = { '-1': 'unstarted', '0': 'ended', '1': 'playing', '2': 'paused', '3': 'buffering', '5': 'cued' };
  var player = null;
  var pendingPause = null;

  function send(message) { host.postMessage(message); }
  function ms(seconds) { return Math.max(0, Math.floor((Number(seconds) || 0) * 1000)); }

  function snapshot() {
    if (!player || typeof player.getCurrentTime !== 'function') { return {}; }
    return {
      positionMs: ms(player.getCurrentTime()),
      durationMs: ms(player.getDuration()),
      state: STATES[String(player.getPlayerState())] || 'unstarted'
    };
  }

  function report(type, extra) {
    var message = snapshot();
    message.type = type;
    for (var key in extra) { if (Object.prototype.hasOwnProperty.call(extra, key)) { message[key] = extra[key]; } }
    send(message);
  }

  function answerPause(requestId) {
    if (pendingPause === requestId) {
      pendingPause = null;
      report('time', { requestId: requestId });
    }
  }

  window.onYouTubeIframeAPIReady = function () { send({ type: 'apiReady' }); };
  var script = document.createElement('script');
  script.src = 'https://www.youtube.com/iframe_api';
  script.onerror = function () { send({ type: 'apiFailed' }); };
  document.head.appendChild(script);

  function load(videoId, startMs) {
    if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) { send({ type: 'error', code: 2 }); return; }
    if (player) { player.destroy(); player = null; }
    pendingPause = null;
    var target = document.createElement('div');
    target.id = 'player';
    document.body.replaceChildren(target);
    player = new YT.Player('player', {
      host: 'https://www.youtube-nocookie.com',
      videoId: videoId,
      width: '100%',
      height: '100%',
      playerVars: {
        autoplay: 0, controls: 1, disablekb: 1, fs: 0, rel: 0, iv_load_policy: 3,
        playsinline: 1, start: Math.floor(startMs / 1000), origin: window.location.origin
      },
      events: {
        onReady: function () {
          var data = typeof player.getVideoData === 'function' ? player.getVideoData() : null;
          report('ready', { title: (data && data.title) || '' });
        },
        onStateChange: function (event) {
          report('state');
          if (event.data === 2 && pendingPause) { answerPause(pendingPause); }
        },
        onError: function (event) { send({ type: 'error', code: Number(event.data) }); }
      }
    });
  }

  host.addEventListener('message', function (event) {
    var m = event.data;
    if (!m || typeof m !== 'object') { return; }
    switch (m.type) {
      case 'load': load(String(m.videoId || ''), Number(m.startMs) || 0); break;
      case 'play': if (player) { player.playVideo(); } break;
      case 'pause': if (player) { player.pauseVideo(); } break;
      case 'seek':
        if (player) {
          player.seekTo(Math.max(0, Number(m.positionMs) || 0) / 1000, true);
          if (m.play) { player.playVideo(); } else { player.pauseVideo(); }
        }
        break;
      case 'seekBy':
        if (player) { player.seekTo(Math.max(0, player.getCurrentTime() + (Number(m.deltaMs) || 0) / 1000), true); }
        break;
      case 'pauseAndReport':
        if (!player) { break; }
        var requestId = String(m.requestId);
        var state = player.getPlayerState();
        if (state === 1 || state === 3) {
          // Answer from the paused state; if the player never reports it,
          // answer anyway after a second so the question is not held up.
          pendingPause = requestId;
          player.pauseVideo();
          setTimeout(function () { answerPause(requestId); }, 1000);
        } else {
          report('time', { requestId: requestId });
        }
        break;
      case 'time': if (player) { report('time', { requestId: String(m.requestId) }); } break;
    }
  });
}());
