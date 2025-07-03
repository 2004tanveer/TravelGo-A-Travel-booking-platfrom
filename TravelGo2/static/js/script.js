document.addEventListener('DOMContentLoaded', () => {
    // Mobile Menu Toggle
    const mobileMenuBtn = document.getElementById('mobileMenuBtn');
    const mobileMenu = document.getElementById('mobileMenu');
    const menuIcon = document.getElementById('menuIcon');
    const closeIcon = document.getElementById('closeIcon');

    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener('click', () => {
            mobileMenu.classList.toggle('active');
            menuIcon.style.display = mobileMenu.classList.contains('active') ? 'none' : 'block';
            closeIcon.style.display = mobileMenu.classList.contains('active') ? 'block' : 'none';
        });
    }

    // Booking Tabs Logic
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabForms = document.querySelectorAll('.tab-content'); // Renamed from tabContents to tabForms for clarity

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabId = button.dataset.tab; // e.g., 'flights', 'hotels'

            // Remove 'active' from all tab buttons and forms
            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabForms.forEach(form => form.classList.remove('active'));

            // Add 'active' to the clicked button
            button.classList.add('active');

            // Show the corresponding form
            const activeForm = document.getElementById(`${tabId}-form`);
            if (activeForm) {
                activeForm.classList.add('active');
                // Update the text of the search button within the active form
                const searchButtonInForm = activeForm.querySelector('.search-btn');
                if (searchButtonInForm) {
                    searchButtonInForm.textContent = `Search ${tabId.charAt(0).toUpperCase() + tabId.slice(1)}`;
                }
            }
        });
    });

    // Initialize Lucide icons on page load
    lucide.createIcons();

    // Optional: Ensure the correct search button text is set on initial page load
    const initialActiveTabBtn = document.querySelector('.tab-btn.active');
    if (initialActiveTabBtn) {
        const initialTabId = initialActiveTabBtn.dataset.tab;
        const initialActiveForm = document.getElementById(`${initialTabId}-form`);
        if (initialActiveForm) {
            const searchButtonInForm = initialActiveForm.querySelector('.search-btn');
            if (searchButtonInForm) {
                searchButtonInForm.textContent = `Search ${initialTabId.charAt(0).toUpperCase() + initialTabId.slice(1)}`;
            }
        }
    }

    // Auto-hide flash messages after a few seconds
    const flashMessagesContainer = document.querySelector('.flash-messages');
    if (flashMessagesContainer) {
        // Set a timeout to remove the whole container, which hides individual messages
        setTimeout(() => {
            flashMessagesContainer.style.display = 'none';
        }, 5000); // Hide after 5 seconds
    }
});