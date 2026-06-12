// Admission AI Chat Client

// ── Whale mascot (SVG-based) ──────────────────────────────────────────────────
// whale-mascot.svg = vectorized SVG from whale.png (vtracer, BFS bg removal), served at /whale-mascot.svg

/** Static avatar used in every AI message row */
const WHALE_AVATAR_SVG = `<img src="/whale-mascot.svg" style="width:100%;height:100%;object-fit:contain;" alt="">`;

function whaleSpouting(statusText = '') {
  const label = statusText
    ? `<span class="text-gray-500 text-xs">${statusText}</span>`
    : '';
  return `<div class="flex items-center gap-2 py-0.5">
    <video src="/whale%20animation.mp4" autoplay loop muted playsinline
           width="68" height="68"
           style="display:block;flex-shrink:0;object-fit:contain;"></video>
    ${label}
  </div>`;
}

// ── App state ─────────────────────────────────────────────────────────────────

const state = {
  user: null,
  history: [],   // [{role: 'user'|'model', parts: [string]}]
  loading: false,
  adConfig: null,          // {adsense_pub_id, adsense_rewarded_slot, ad_credits_max}
  pendingMessage: null,    // message to retry after ad reward
  questionCount: 0,        // questions asked this session (for survey trigger)
  surveySeen: false,       // whether survey has been shown this session
};

// ── Initialization ────────────────────────────────────────────────────────────

async function init() {
  try {
    const [meResp, cfgResp] = await Promise.all([
      fetch('/api/me'),
      fetch('/api/config'),
    ]);
    if (cfgResp.ok) {
      state.adConfig = await cfgResp.json();
      _initAdSense();
      if (state.adConfig.show_beta_banner) {
        openBetaBanner();
      }
    }
    if (meResp.ok) {
      state.user = await meResp.json();
      _loadHistory();
    }
    // Always show chat screen — auth is optional
    showChatScreen();
    await refreshUsage();
  } catch {
    showChatScreen();
  }
}

// ── History persistence ───────────────────────────────────────────────────────

const HISTORY_KEY = 'chat_history_v1';
const MESSAGES_KEY = 'chat_messages_v1';
const MAX_SAVED_TURNS = 20;

function _saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(-MAX_SAVED_TURNS)));
    // Save display messages (skip welcome message at index 0)
    const msgs = [];
    const container = document.getElementById('messages');
    container.querySelectorAll('[data-save]').forEach(el => {
      msgs.push({ role: el.dataset.role, html: el.innerHTML });
    });
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(msgs.slice(-MAX_SAVED_TURNS * 2)));
  } catch {}
}

function _loadHistory() {
  try {
    const h = localStorage.getItem(HISTORY_KEY);
    if (h) state.history = JSON.parse(h);
    const m = localStorage.getItem(MESSAGES_KEY);
    if (!m) return;
    const msgs = JSON.parse(m);
    if (!msgs.length) return;
    const container = document.getElementById('messages');
    msgs.forEach(({ role, html }) => {
      const wrapper = document.createElement('div');
      wrapper.className = `flex gap-3 ${role === 'user' ? 'justify-end' : 'justify-start'}`;
      wrapper.dataset.save = '1';
      wrapper.dataset.role = role;
      wrapper.innerHTML = html;
      container.appendChild(wrapper);
    });
    scrollToBottom();
  } catch {}
}

function _initAdSense() {
  const pubId = state.adConfig?.adsense_pub_id;
  if (!pubId || document.getElementById('adsense-script')) return;
  const s = document.createElement('script');
  s.id = 'adsense-script';
  s.async = true;
  s.crossOrigin = 'anonymous';
  s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${pubId}`;
  document.head.appendChild(s);
}

function showChatScreen() {
  const chatScreen = document.getElementById('chat-screen');
  chatScreen.classList.remove('hidden');
  chatScreen.classList.add('flex');

  const u = state.user;
  const isLoggedIn = !!u;

  // Toggle user info vs anonymous sections
  document.getElementById('user-info-section').classList.toggle('hidden', !isLoggedIn);
  document.getElementById('anon-login-section').classList.toggle('hidden', isLoggedIn);
  document.getElementById('logout-section').classList.toggle('hidden', !isLoggedIn);
  document.getElementById('profile-btn')?.classList.toggle('hidden', !isLoggedIn);

  if (isLoggedIn) {
    const pic = u.picture || '';
    if (pic) {
      document.getElementById('user-avatar').src = pic;
      document.getElementById('user-avatar-mobile').src = pic;
    }
    document.getElementById('user-name').textContent = u.name || u.email || '';
    document.getElementById('user-email').textContent = u.email || '';
  }

  // Update tier badge
  const tier = u?.tier || 'free';
  const badge = document.getElementById('tier-badge');
  const upgradeBtn = document.getElementById('upgrade-btn');
  const manageBtn = document.getElementById('manage-btn');
  const paymentsEnabled = state.adConfig?.payments_enabled === true;
  if (tier === 'paid') {
    badge.textContent = '프리미엄';
    badge.className = 'text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700';
    upgradeBtn.classList.add('hidden');
    manageBtn.classList.toggle('hidden', !paymentsEnabled);
  } else {
    badge.textContent = '베타';
    badge.className = 'text-xs font-semibold px-2 py-0.5 rounded-full bg-green-100 text-green-700';
    upgradeBtn.classList.toggle('hidden', !paymentsEnabled);
    manageBtn.classList.add('hidden');
  }

  // Check for payment result in URL
  const params = new URLSearchParams(window.location.search);
  if (params.get('payment') === 'success') {
    appendMessage('model', '✅ 프리미엄 결제가 완료되었습니다! Claude AI로 더 정확한 상담을 받으실 수 있습니다.');
    history.replaceState({}, '', '/');
    refreshUsage();
  } else if (params.get('payment') === 'fail') {
    appendMessage('model', `⚠️ 결제에 실패했습니다. 다시 시도해 주세요. (오류: ${params.get('error') || '알 수 없음'})`);
    history.replaceState({}, '', '/');
  } else if (params.get('error')) {
    appendMessage('model', `로그인 오류가 발생했습니다: ${params.get('error')}`);
    history.replaceState({}, '', '/');
  }

  document.getElementById('input').focus();
}

// ── Auth modal ─────────────────────────────────────────────────────────────────

let _pendingEmail = '';

function openAuthModal() {
  showAuthView('main');
  document.getElementById('auth-modal').classList.remove('hidden');
}

function closeAuthModal() {
  document.getElementById('auth-modal').classList.add('hidden');
}

function showAuthView(view) {
  ['main','signup','verify','login-email'].forEach(v => {
    document.getElementById(`auth-view-${v}`).classList.add('hidden');
  });
  document.getElementById(`auth-view-${view}`).classList.remove('hidden');
}

async function submitSignup() {
  const email = document.getElementById('signup-email').value.trim();
  const password = document.getElementById('signup-password').value;
  const errEl = document.getElementById('signup-error');
  errEl.classList.add('hidden');
  try {
    const resp = await fetch('/api/auth/register', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password}),
    });
    const data = await resp.json();
    if (!resp.ok) { errEl.textContent = data.detail || '오류가 발생했습니다.'; errEl.classList.remove('hidden'); return; }
    _pendingEmail = email;
    document.getElementById('verify-desc').textContent = `${email}로 발송된 6자리 인증 코드를 입력해 주세요.`;
    showAuthView('verify');
  } catch { errEl.textContent = '네트워크 오류가 발생했습니다.'; errEl.classList.remove('hidden'); }
}

async function submitVerify() {
  const code = document.getElementById('verify-code').value.trim();
  const errEl = document.getElementById('verify-error');
  errEl.classList.add('hidden');
  try {
    const resp = await fetch('/api/auth/verify-email', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: _pendingEmail, code}),
    });
    const data = await resp.json();
    if (!resp.ok) { errEl.textContent = data.detail || '오류가 발생했습니다.'; errEl.classList.remove('hidden'); return; }
    // Session cookie set by server — reload user
    closeAuthModal();
    const meResp = await fetch('/api/me');
    if (meResp.ok) { state.user = await meResp.json(); showChatScreen(); refreshUsage(); }
  } catch { errEl.textContent = '네트워크 오류가 발생했습니다.'; errEl.classList.remove('hidden'); }
}

async function resendCode() {
  await fetch('/api/auth/resend-verification', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: _pendingEmail, code: ''}),
  });
  alert('인증 코드를 다시 발송했습니다.');
}

async function submitEmailLogin() {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');
  errEl.classList.add('hidden');
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password}),
    });
    const data = await resp.json();
    if (!resp.ok) { errEl.textContent = data.detail || '오류가 발생했습니다.'; errEl.classList.remove('hidden'); return; }
    closeAuthModal();
    const meResp = await fetch('/api/me');
    if (meResp.ok) { state.user = await meResp.json(); showChatScreen(); refreshUsage(); }
  } catch { errEl.textContent = '네트워크 오류가 발생했습니다.'; errEl.classList.remove('hidden'); }
}


// ── Usage display ─────────────────────────────────────────────────────────────

async function refreshUsage() {
  try {
    const resp = await fetch('/api/usage');
    if (!resp.ok) return;
    const data = await resp.json();
    const dailyUsed = data.daily_used ?? data.used ?? 0;
    const dailyLimit = data.daily_limit ?? data.limit ?? 1;
    const credits = data.ad_credits || 0;
    const effectiveUsed = dailyUsed - credits;  // credits expand capacity
    const pct = Math.round((effectiveUsed / dailyLimit) * 100);
    document.getElementById('usage-bar').style.width = Math.min(pct, 100) + '%';
    const creditSuffix = credits > 0 ? ` (+${credits})` : '';
    document.getElementById('usage-text').textContent = `${effectiveUsed}/${dailyLimit}${creditSuffix}`;

    const monthlyEl = document.getElementById('monthly-usage');
    if (data.monthly_limit) {
      monthlyEl.textContent = `이번 달 ${data.monthly_used}/${data.monthly_limit}문항`;
      monthlyEl.classList.remove('hidden');
    } else {
      monthlyEl.classList.add('hidden');
    }

    // Show "Watch Ad" button for free users when daily limit is reached and ads available
    const tier = state.user?.tier || 'free';
    const adMax = state.adConfig?.ad_credits_max ?? 4;
    const limitHit = (effectiveUsed >= dailyLimit) && credits === 0;
    const canEarnAd = tier === 'free' && credits < adMax;
    const watchAdBtn = document.getElementById('watch-ad-btn');
    const devMode = state.adConfig?.dev_mode ?? false;
    if (watchAdBtn) {
      if (limitHit && canEarnAd) {
        watchAdBtn.classList.remove('hidden');
        if (devMode) {
          watchAdBtn.textContent = '🛠️ 계속하기 (개발 모드)';
          watchAdBtn.onclick = () => grantDevCredit(null);
        } else {
          watchAdBtn.textContent = `📺 광고 보고 추가 질문하기 (${adMax - credits}회 가능)`;
          watchAdBtn.onclick = () => openAdModal(null);
        }
      } else {
        watchAdBtn.classList.add('hidden');
      }
    }
  } catch {}
}

// ── Upgrade modal ─────────────────────────────────────────────────────────────

function openUpgradeModal() {
  if (!state.adConfig?.payments_enabled) return;
  document.getElementById('upgrade-modal').classList.remove('hidden');
}

function closeUpgradeModal() {
  document.getElementById('upgrade-modal').classList.add('hidden');
}

async function startSubscription() {
  try {
    const resp = await fetch('/api/subscribe', { method: 'POST' });
    if (resp.status === 401) {
      closeUpgradeModal();
      showLoginScreen();
      return;
    }
    if (!resp.ok) {
      alert('결제 서비스를 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.');
      return;
    }
    const data = await resp.json();
    // Use TossPayments SDK for billing key issuance
    if (typeof TossPayments !== 'undefined') {
      const tossPayments = TossPayments(data.clientKey);
      await tossPayments.requestBillingAuth('카드', {
        customerKey: data.customerKey,
        successUrl: data.successUrl,
        failUrl: data.failUrl,
      });
    } else {
      // Fallback: redirect to success URL directly (for testing without Toss SDK)
      alert('Toss Payments SDK가 로드되지 않았습니다. TOSS_CLIENT_KEY를 확인해 주세요.');
    }
  } catch (err) {
    alert(`결제 오류: ${err.message}`);
  }
}

// ── Chat ──────────────────────────────────────────────────────────────────────

// ── Sidebar (mobile) ──────────────────────────────────────────────────────────

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  const isOpen = !sidebar.classList.contains('-translate-x-full');
  if (isOpen) {
    sidebar.classList.add('-translate-x-full');
    backdrop.classList.add('hidden');
  } else {
    sidebar.classList.remove('-translate-x-full');
    backdrop.classList.remove('hidden');
  }
}

function closeSidebar() {
  document.getElementById('sidebar').classList.add('-translate-x-full');
  document.getElementById('sidebar-backdrop').classList.add('hidden');
}

// ── Ad reward flow ────────────────────────────────────────────────────────────

async function grantDevCredit(pendingMsg) {
  try {
    const resp = await fetch('/api/credits/ad', { method: 'POST' });
    const data = await resp.json();
    if (data.ad_credits !== undefined) {
      await refreshUsage();
      if (pendingMsg) {
        document.getElementById('input').value = pendingMsg;
        autoResize(document.getElementById('input'));
        sendMessage();
      }
    }
  } catch {}
}

function openAdModal(pendingMsg) {
  if (pendingMsg) state.pendingMessage = pendingMsg;
  const adMax = state.adConfig?.ad_credits_max ?? 4;
  document.getElementById('ad-modal-desc').textContent =
    `광고를 시청하면 질문 1개를 추가로 받을 수 있습니다. (오늘 최대 ${adMax}회)`;
  document.getElementById('ad-modal').classList.remove('hidden');
  document.getElementById('ad-countdown').classList.add('hidden');
  document.getElementById('ad-watch-btn').disabled = false;
  document.getElementById('ad-watch-btn').textContent = '광고 보기';
}

function closeAdModal() {
  document.getElementById('ad-modal').classList.add('hidden');
  document.getElementById('ad-countdown').classList.add('hidden');
  document.getElementById('ad-container').classList.add('hidden');
  state.pendingMessage = null;
}

async function startWatchAd() {
  const btn = document.getElementById('ad-watch-btn');
  btn.disabled = true;

  const pubId = state.adConfig?.adsense_pub_id;
  const slot = state.adConfig?.adsense_rewarded_slot;

  if (pubId && slot && typeof adsbygoogle !== 'undefined') {
    // Real Google AdSense rewarded ad
    await _showAdSenseRewarded(pubId, slot);
  } else {
    // Development fallback: timed countdown (no real ad)
    await _showCountdownAd();
  }
}

function _showAdSenseRewarded(pubId, slot) {
  return new Promise((resolve) => {
    const ins = document.getElementById('adsense-ad');
    ins.setAttribute('data-ad-client', pubId);
    ins.setAttribute('data-ad-slot', slot);
    ins.setAttribute('data-ad-format', 'rewarded');
    document.getElementById('ad-container').classList.remove('hidden');

    (window.adsbygoogle = window.adsbygoogle || []).push({
      params: {
        google_ad_client: pubId,
        google_ad_slot: slot,
      },
      onAllImpressions: () => {},
      onUserEarnedReward: () => {
        _onAdCompleted();
        resolve();
      },
      onAdDismissed: () => resolve(),
      onAdFailedToLoad: () => {
        // Fallback to countdown if ad fails
        document.getElementById('ad-container').classList.add('hidden');
        _showCountdownAd().then(resolve);
      },
    });
  });
}

function _showCountdownAd() {
  const SECONDS = 5;
  return new Promise((resolve) => {
    const countdown = document.getElementById('ad-countdown');
    const timerEl = document.getElementById('ad-timer');
    const progress = document.getElementById('ad-progress');
    countdown.classList.remove('hidden');
    document.getElementById('ad-modal-icon').textContent = '⏳';

    let remaining = SECONDS;
    timerEl.textContent = remaining;
    progress.style.width = '0%';

    const interval = setInterval(() => {
      remaining--;
      timerEl.textContent = remaining;
      progress.style.width = `${((SECONDS - remaining) / SECONDS) * 100}%`;
      if (remaining <= 0) {
        clearInterval(interval);
        countdown.classList.add('hidden');
        _onAdCompleted();
        resolve();
      }
    }, 1000);
  });
}

async function _onAdCompleted() {
  try {
    const resp = await fetch('/api/credits/ad', { method: 'POST' });
    const data = await resp.json();
    const credits = data.ad_credits || 0;
    const canMore = data.can_watch_more;

    document.getElementById('ad-modal-icon').textContent = '✅';
    document.getElementById('ad-modal-title').textContent = '1문항이 추가되었습니다!';
    document.getElementById('ad-modal-desc').textContent =
      canMore ? `오늘 ${credits}문항 추가 사용 가능` : '오늘 광고 추가 질문 한도에 도달했습니다.';

    await refreshUsage();

    // Auto-retry pending message after short delay
    const pending = state.pendingMessage;
    if (pending) {
      setTimeout(() => {
        closeAdModal();
        document.getElementById('input').value = pending;
        autoResize(document.getElementById('input'));
        sendMessage();
      }, 1200);
    } else {
      setTimeout(closeAdModal, 2000);
    }
  } catch {
    closeAdModal();
  }
}

// ── Profile modal ─────────────────────────────────────────────────────────────

let _profileState = { gender: '', school_type: '', track: '' };

function _highlight(id) {
  document.getElementById(id)?.classList.add('bg-blue-100', 'border-blue-400');
}
function _clearHighlights(ids) {
  ids.forEach(id => document.getElementById(id)?.classList.remove('bg-blue-100', 'border-blue-400'));
}
function setGender(v) {
  _profileState.gender = v;
  _clearHighlights(['gender-male','gender-female','gender-none']);
  if (v === '남') _highlight('gender-male');
  else if (v === '여') _highlight('gender-female');
  else _highlight('gender-none');
}
function setSchoolType(v) {
  _profileState.school_type = v;
  _clearHighlights(['school-urban','school-rural']);
  _highlight(v === 'urban' ? 'school-urban' : 'school-rural');
}
function setTrack(v) {
  _profileState.track = v;
  _clearHighlights(['track-natural','track-human','track-art']);
  const map = {'자연':'track-natural','인문':'track-human','예체능':'track-art'};
  if (map[v]) _highlight(map[v]);
}

async function openProfileModal() {
  if (!state.user) { openAuthModal(); return; }
  document.getElementById('profile-success').classList.add('hidden');
  try {
    const resp = await fetch('/api/profile');
    if (resp.ok) {
      const p = await resp.json();
      document.getElementById('profile-school-name').value = p.school_name || '';
      document.getElementById('profile-school-region').value = p.school_region || '';
      document.getElementById('profile-grad-year').value = p.graduation_year || '';
      let interests = p.interests || '';
      try { const arr = JSON.parse(interests); interests = arr.join(', '); } catch {}
      document.getElementById('profile-interests').value = interests;
      if (p.gender) setGender(p.gender);
      if (p.school_type) setSchoolType(p.school_type);
      if (p.track) setTrack(p.track);
    }
  } catch {}
  document.getElementById('profile-modal').classList.remove('hidden');
}

function closeProfileModal() {
  document.getElementById('profile-modal').classList.add('hidden');
}

async function saveProfile() {
  const interests = document.getElementById('profile-interests').value
    .split(',').map(s => s.trim()).filter(Boolean);
  const gradYearStr = document.getElementById('profile-grad-year').value;
  const body = {
    gender: _profileState.gender || null,
    school_name: document.getElementById('profile-school-name').value.trim() || null,
    school_region: document.getElementById('profile-school-region').value.trim() || null,
    school_type: _profileState.school_type || null,
    graduation_year: gradYearStr ? parseInt(gradYearStr) : null,
    track: _profileState.track || null,
    interests: interests.length ? interests : null,
  };
  Object.keys(body).forEach(k => body[k] === null && delete body[k]);
  try {
    await fetch('/api/profile', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    document.getElementById('profile-success').classList.remove('hidden');
    setTimeout(() => closeProfileModal(), 1200);
  } catch {}
}

// ── Chat ──────────────────────────────────────────────────────────────────────

function clearChat() {
  state.history = [];
  localStorage.removeItem(HISTORY_KEY);
  localStorage.removeItem(MESSAGES_KEY);
  const messages = document.getElementById('messages');
  while (messages.children.length > 1) {
    messages.removeChild(messages.lastChild);
  }
  closeSidebar();
  document.getElementById('input').focus();
}

async function sendMessage() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text || state.loading) return;

  input.value = '';
  autoResize(input);
  setLoading(true);

  appendMessage('user', text);
  state.history.push({ role: 'user', parts: [text] });

  state.questionCount++;
  if (state.questionCount === 3 && state.adConfig?.show_survey && !state.surveySeen) {
    state.surveySeen = true;
    openSurveyModal();
  }

  const aiBubble = createAIBubble();
  let fullText = '';
  let errorHandled = false;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        history: state.history.slice(0, -1),
      }),
    });

    if (resp.status === 401) {
      aiBubble.innerHTML = '<p class="text-red-500 text-sm">세션이 만료되었습니다. 새로고침 후 다시 로그인해 주세요.</p>';
      setLoading(false);
      return;
    }

    if (resp.status === 429) {
      const err = await resp.json();
      const tier = state.user?.tier || 'free';
      const adMax = state.adConfig?.ad_credits_max ?? 4;
      const devMode = state.adConfig?.dev_mode ?? false;
      if (tier === 'free') {
        if (devMode) {
          aiBubble.innerHTML = `
            <p class="text-orange-500 text-sm mb-2">⚠️ ${err.detail}</p>
            <button onclick="grantDevCredit(${JSON.stringify(text).replace(/"/g, '&quot;')})"
                    class="text-sm bg-gray-500 hover:bg-gray-600 text-white px-3 py-1.5 rounded-lg transition">
              🛠️ 계속하기 (개발 모드)
            </button>`;
        } else {
          aiBubble.innerHTML = `
            <p class="text-orange-500 text-sm mb-2">⚠️ ${err.detail}</p>
            <div class="flex flex-col gap-1">
              <button onclick="openAdModal(${JSON.stringify(text).replace(/"/g, '&quot;')})"
                      class="text-sm bg-purple-500 hover:bg-purple-600 text-white px-3 py-1.5 rounded-lg transition">
                📺 광고 보고 추가 질문하기 (하루 최대 ${adMax}회)
              </button>
              <button onclick="openUpgradeModal()"
                      class="text-sm text-blue-600 hover:underline text-left px-1">
                프리미엄 구독으로 하루 5문항 이용하기 →
              </button>
            </div>`;
        }
      } else {
        aiBubble.innerHTML = `<p class="text-orange-500 text-sm">⚠️ ${err.detail}</p>`;
      }
      setLoading(false);
      await refreshUsage();
      return;
    }

    if (!resp.ok) {
      aiBubble.innerHTML = '<p class="text-red-500 text-sm">오류가 발생했습니다. 잠시 후 다시 시도해 주세요.</p>';
      setLoading(false);
      return;
    }

    // Read SSE stream
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    outer: while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const chunk = line.slice(6);
        if (chunk === '[DONE]') break outer;
        try {
          const parsed = JSON.parse(chunk);
          if (parsed && typeof parsed === 'object' && parsed.status) {
            aiBubble.innerHTML = whaleSpouting(escapeHtml(parsed.status));
            scrollToBottom();
          } else if (parsed && typeof parsed === 'object' && parsed.error) {
            _showRetryBubble(aiBubble, text, parsed.error);
            fullText = '';
            errorHandled = true;
            break outer;
          } else {
            const chunk = typeof parsed === 'string' ? parsed : JSON.stringify(parsed);
            fullText += chunk;
            renderMarkdown(aiBubble, fullText);
            scrollToBottom();
          }
        } catch {
          // Plain text (non-JSON) — display directly
          fullText += chunk;
          renderMarkdown(aiBubble, fullText);
          scrollToBottom();
        }
      }
    }

    if (fullText) {
      state.history.push({ role: 'model', parts: [fullText] });
      _saveHistory();
    } else if (!errorHandled) {
      _showRetryBubble(aiBubble, text, '응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.');
    }

  } catch (err) {
    _showRetryBubble(aiBubble, text, '연결 오류가 발생했습니다.');
  }

  setLoading(false);
  await refreshUsage();
}

function askSample(btn) {
  const input = document.getElementById('input');
  input.value = btn.textContent.trim();
  autoResize(input);
  closeSidebar();
  input.focus();
}

// ── Rendering helpers ─────────────────────────────────────────────────────────

function appendMessage(role, text) {
  const messages = document.getElementById('messages');
  const wrapper = document.createElement('div');
  wrapper.className = `flex gap-3 ${role === 'user' ? 'justify-end' : 'justify-start'}`;
  wrapper.dataset.save = '1';
  wrapper.dataset.role = role;

  if (role === 'user') {
    wrapper.innerHTML = `
      <div class="chat-bubble bg-blue-500 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm">${escapeHtml(text)}</div>
      <div class="w-8 h-8 rounded-full bg-gray-300 flex items-center justify-center text-gray-600 text-xs shrink-0 overflow-hidden">
        ${state.user?.picture ? `<img src="${state.user.picture}" class="w-full h-full object-cover">` : 'YOU'}
      </div>`;
  }

  messages.appendChild(wrapper);
  scrollToBottom();
  return wrapper;
}

function createAIBubble() {
  const messages = document.getElementById('messages');
  const wrapper = document.createElement('div');
  wrapper.className = 'flex gap-3 justify-start';
  wrapper.dataset.save = '1';
  wrapper.dataset.role = 'model';

  const avatar = document.createElement('div');
  avatar.className = 'w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center shrink-0 overflow-hidden';
  avatar.innerHTML = WHALE_AVATAR_SVG;

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble bg-white rounded-2xl rounded-tl-sm shadow-sm p-4 text-sm prose max-w-none';
  bubble.innerHTML = whaleSpouting();

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  messages.appendChild(wrapper);
  scrollToBottom();
  return bubble;
}

function _showRetryBubble(bubble, originalText, reason) {
  bubble.innerHTML = `
    <p class="text-orange-500 text-sm mb-2">⚠️ ${escapeHtml(reason)}</p>
    <button onclick="retryMessage(${JSON.stringify(originalText).replace(/"/g, '&quot;')}, this)"
            class="text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 px-3 py-1.5 rounded-lg transition">
      🔄 다시 시도
    </button>`;
}

function retryMessage(text, btn) {
  // Remove the failed bubble's wrapper and re-send
  const wrapper = btn.closest('[data-save]');
  if (wrapper) wrapper.remove();
  // Also remove the last user bubble if it matches (avoid duplicate)
  const messages = document.getElementById('messages');
  const saved = [...messages.querySelectorAll('[data-save][data-role="user"]')];
  if (saved.length && saved[saved.length - 1].textContent.trim() === text.trim()) {
    saved[saved.length - 1].remove();
  }
  // Pop both turns from history so they're not double-sent
  if (state.history.length >= 2 && state.history[state.history.length - 1].role === 'model') {
    state.history.pop();
  }
  if (state.history.length >= 1 && state.history[state.history.length - 1].role === 'user') {
    state.history.pop();
  }
  document.getElementById('input').value = text;
  autoResize(document.getElementById('input'));
  sendMessage();
}

function renderMarkdown(el, text) {
  if (typeof marked !== 'undefined') {
    el.innerHTML = marked.parse(text);
    // Wrap tables in a scrollable div so they scroll within the bubble on mobile
    el.querySelectorAll('table').forEach(t => {
      if (t.parentElement.classList.contains('table-scroll-wrap')) return;
      const wrap = document.createElement('div');
      wrap.className = 'table-scroll-wrap';
      t.parentNode.insertBefore(wrap, t);
      wrap.appendChild(t);
    });
  } else {
    el.textContent = text;
  }
}

function scrollToBottom() {
  const messages = document.getElementById('messages');
  messages.scrollTop = messages.scrollHeight;
}

function setLoading(loading) {
  state.loading = loading;
  const btn = document.getElementById('send-btn');
  const input = document.getElementById('input');
  btn.disabled = loading;
  input.disabled = loading;
  if (!loading) input.focus();
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 128) + 'px';
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

// ── Beta Banner ───────────────────────────────────────────────────────────────

function openBetaBanner() {
  document.getElementById('beta-banner-modal').classList.remove('hidden');
}

function closeBetaBanner() {
  document.getElementById('beta-banner-modal').classList.add('hidden');
}

// ── Survey Modal ──────────────────────────────────────────────────────────────

function openSurveyModal() {
  document.getElementById('survey-modal').classList.remove('hidden');
}

function closeSurveyModal() {
  document.getElementById('survey-modal').classList.add('hidden');
}

async function submitSurvey() {
  const ratingEl = document.querySelector('input[name="survey-rating"]:checked');
  const rating = ratingEl ? parseInt(ratingEl.value) : null;
  const improvement = document.getElementById('survey-improvement').value.trim();
  const other = document.getElementById('survey-other').value.trim();
  try {
    await fetch('/api/survey', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, improvement, other }),
    });
  } catch {}
  closeSurveyModal();
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
init();
