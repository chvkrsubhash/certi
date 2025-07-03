document.addEventListener('DOMContentLoaded', () => {
    const hamburger = document.querySelector('.hamburger');
    const navLinks = document.querySelector('.nav-links');

    hamburger.addEventListener('click', () => {
        navLinks.classList.toggle('active');
    });

    // Timer for exam page
    const timerElement = document.querySelector('#timer');
    if (timerElement) {
        let timeLeft = 3600; // 1 hour in seconds
        const timer = setInterval(() => {
            let minutes = Math.floor(timeLeft / 60);
            let seconds = timeLeft % 60;
            timerElement.textContent = `Time Left: ${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
            timeLeft--;
            if (timeLeft < 0) {
                clearInterval(timer);
                alert('Time is up!');
                document.querySelector('form').submit();
            }
        }, 1000);
    }
});