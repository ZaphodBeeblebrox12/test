/**
 * Referral Celebration Effects
 * Triggers toasts, confetti, and animations on reward events
 */

(function() {
    'use strict';

    // Toast container setup
    function ensureToastContainer() {
        let container = document.getElementById('referralToastContainer');
        if (!container) {
            container = document.createElement('div');
            container.id = 'referralToastContainer';
            container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
            container.style.zIndex = '1055';
            document.body.appendChild(container);
        }
        return container;
    }

    // Show referral toast
    window.showReferralToast = function(message, type = 'success', title = '') {
        const container = ensureToastContainer();
        const id = 'referral-toast-' + Date.now();

        const icons = {
            success: 'bi-gift-fill',
            info: 'bi-info-circle',
            warning: 'bi-exclamation-triangle',
            celebration: 'bi-stars'
        };

        const bgColors = {
            success: 'bg-success',
            info: 'bg-primary',
            warning: 'bg-warning text-dark',
            celebration: 'bg-success'
        };

        const html = `
            <div id="${id}" class="toast align-items-center text-white ${bgColors[type]} border-0" role="alert">
                <div class="d-flex">
                    <div class="toast-body">
                        <i class="bi ${icons[type]} me-2"></i>
                        ${title ? `<strong>${title}</strong><br>` : ''}
                        ${message}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            </div>
        `;

        container.insertAdjacentHTML('beforeend', html);
        const el = document.getElementById(id);
        const toast = new bootstrap.Toast(el, { delay: 6000 });
        toast.show();

        el.addEventListener('hidden.bs.toast', () => el.remove());
    };

    // Celebration with confetti
    window.celebrateReferralReward = function(amount, days) {
        // Show toast
        window.showReferralToast(
            `+$${amount} credit unlocked! Your subscription is extended by ${days} days.`,
            'celebration',
            '🎉 Reward Unlocked!'
        );

        // Trigger confetti if library available
        if (typeof confetti === 'function') {
            const duration = 3000;
            const end = Date.now() + duration;

            (function frame() {
                confetti({
                    particleCount: 5,
                    angle: 60,
                    spread: 55,
                    origin: { x: 0 },
                    colors: ['#198754', '#20c997', '#ffc107']
                });
                confetti({
                    particleCount: 5,
                    angle: 120,
                    spread: 55,
                    origin: { x: 1 },
                    colors: ['#198754', '#20c997', '#ffc107']
                });

                if (Date.now() < end) {
                    requestAnimationFrame(frame);
                }
            }());
        }

        // Animate balance if visible
        const balanceEl = document.getElementById('referralBalance');
        if (balanceEl) {
            balanceEl.style.transition = 'transform 0.3s ease';
            balanceEl.style.transform = 'scale(1.2)';
            setTimeout(() => {
                balanceEl.style.transform = 'scale(1)';
            }, 300);
        }
    };

    // Auto-check for updates (polling)
    window.startReferralPolling = function(initialBalance) {
        let lastBalance = initialBalance;

        setInterval(async () => {
            try {
                const response = await fetch('/growth/referrals/api/rewards/');
                if (!response.ok) return;

                const data = await response.json();

                if (data.balance_cents > lastBalance) {
                    const newAmount = ((data.balance_cents - lastBalance) / 100).toFixed(2);
                    const days = Math.floor(newAmount / 0.5);

                    window.celebrateReferralReward(newAmount, days);
                    lastBalance = data.balance_cents;

                    // Update balance display
                    const el = document.getElementById('referralBalance');
                    if (el) el.textContent = data.balance_display;
                }
            } catch (e) {
                console.debug('Referral poll error:', e);
            }
        }, 30000);
    };

    // Initialize on load
    document.addEventListener('DOMContentLoaded', () => {
        const celebrate = document.querySelector('[data-celebrate-referral]');
        if (celebrate) {
            const amount = celebrate.dataset.amount;
            const days = celebrate.dataset.days;
            setTimeout(() => window.celebrateReferralReward(amount, days), 500);
        }
    });
})();