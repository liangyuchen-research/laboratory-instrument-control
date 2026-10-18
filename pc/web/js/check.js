//check.js — "Recheck" (AMENDMENTS M18).
//
// The person switched something at the rig and wants to know now whether the program sees it: the program asks the RPi
// to read the high-voltage supply and the Nano at once (POST /api/rpi/check — read-only, no PIN: nothing is switched,
// written or started) and says in plain words what answered and where to look.
//Used by the footer (frame.js: the button beside "Cannot start") and by the two status cells of overview (pages/dash.js).

let running = false;

// -> the program's reply {ok, busy, rows: [{name, ok, text}], advice}, or null (it could not be asked: the reason was shown).
export async function checkNow(api, dialog) {
  if (running) return null;
  running = true;
  try {
    const reply = await api.post('/api/rpi/check', {}, {timeout: 20000});
    await dialog.message(reply.ok ? "Recheck: All responses" : "Result of recheck", reply.advice || '', {wide: true,
      level: reply.ok ? '' : 'error', lines: (reply.rows || []).map((row) => [row.name, row.text])});
    return reply;
  } catch (error) {
    await dialog.message("Recheck failed", error.message, {level: 'error'});
    return null;
  } finally {
    running = false;
  }
}
