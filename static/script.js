async function loadCurrent() {
    const res = await fetch('/current');
    const data = await res.json();

    document.getElementById('output').textContent =
        JSON.stringify(data, null, 2);
}

async function loadHistory() {
    const res = await fetch('/history');
    const data = await res.json();

    document.getElementById('output').textContent =
        JSON.stringify(data, null, 2);
}
