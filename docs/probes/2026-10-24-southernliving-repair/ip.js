(async () => {
  try {
    const r = await fetch("https://ipinfo.io/json");
    return await r.text();
  } catch (e) {
    return "ERR " + e;
  }
})()
