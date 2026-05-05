const API_BASE_URL = 'http://localhost:8000';
(async () => {
  try {
    console.log("Fetching...");
    const resp = await fetch(`${API_BASE_URL}/capture/session`, { method: 'POST' });
    console.log("Status:", resp.status);
    const data = await resp.json();
    console.log("Data:", data);
  } catch (e) {
    console.error("Error:", e);
  }
})();
