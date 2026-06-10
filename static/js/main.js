/* webadmin-nakitAkim — main.js */
'use strict';

// ── Sidebar Toggle ──────────────────────────────────────────────────────────
const menuToggle = document.getElementById('menuToggle');
const sidebar    = document.getElementById('sidebar');

if (menuToggle && sidebar) {
  menuToggle.addEventListener('click', () => {
    sidebar.classList.toggle('open');
  });
  // Dışına tıklayınca kapat (mobil)
  document.addEventListener('click', (e) => {
    if (window.innerWidth <= 900 &&
        !sidebar.contains(e.target) &&
        !menuToggle.contains(e.target)) {
      sidebar.classList.remove('open');
    }
  });
}

// ── Toast bildirimi ──────────────────────────────────────────────────────────
function showToast(message, duration = 2500) {
  let toast = document.getElementById('toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('show'), duration);
}

// ── DB Bağlantı Testi ─────────────────────────────────────────────────────────
async function testDb() {
  const btn = document.getElementById('testDbBtn');
  const msg = document.getElementById('dbStatusMsg');
  if (btn) btn.textContent = 'Test ediliyor...';
  try {
    const r    = await fetch('/ajax/db-test');
    const data = await r.json();
    if (msg) msg.textContent = data.message;
    // Durum noktasını güncelle
    const dot = document.querySelector('#dbStatusRow .status-dot, .status-dot');
    if (dot) {
      dot.className = `status-dot ${data.success ? 'status-ok' : 'status-err'}`;
    }
  } catch (e) {
    if (msg) msg.textContent = 'Bağlantı isteği başarısız.';
  } finally {
    if (btn) btn.textContent = 'Test Et';
  }
}

// ── Flash mesaj auto-dismiss ──────────────────────────────────────────────────
document.querySelectorAll('.alert').forEach(alert => {
  setTimeout(() => {
    alert.style.opacity = '0';
    alert.style.transition = 'opacity 0.5s';
    setTimeout(() => alert.remove(), 500);
  }, 4000);
});

// ── Otomatik result kartına scroll ───────────────────────────────────────────
const resultCard = document.getElementById('resultCard');
if (resultCard) {
  resultCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Tarih kısıtlaması (bitiş >= başlangıç) ───────────────────────────────────
const startDate = document.getElementById('startDate');
const endDate   = document.getElementById('endDate');
if (startDate && endDate) {
  startDate.addEventListener('change', () => {
    if (endDate.value && endDate.value < startDate.value) {
      endDate.value = startDate.value;
    }
  });
}

// ── Genel yardımcılar ─────────────────────────────────────────────────────────
function togglePassword(id) {
  const input = document.getElementById(id);
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
}

function copyText(text) {
  navigator.clipboard.writeText(text)
    .then(() => showToast('Kopyalandı ✓'))
    .catch(() => showToast('Kopyalama başarısız'));
}
