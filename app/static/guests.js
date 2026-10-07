(function () {
  var KEY = 'guests.showAllExtra';
  var toggle = document.getElementById('toggle-all-extra');
  var items = document.querySelectorAll('details.extra');
  function setAll(open) {
    items.forEach(function (d) { d.open = open; });
  }
  var saved = false;
  try { saved = localStorage.getItem(KEY) === '1'; } catch (e) {}
  if (toggle) {
    toggle.checked = saved;
    toggle.addEventListener('change', function () {
      setAll(toggle.checked);
      try { localStorage.setItem(KEY, toggle.checked ? '1' : '0'); } catch (e) {}
    });
  }
  if (saved) setAll(true);  function pad(n) { return (n < 10 ? '0' : '') + n; }
  document.querySelectorAll('form.comment').forEach(function (f) {
    var btn = f.querySelector('.comment-new');
    var ta = f.querySelector('textarea');
    if (!btn || !ta) return;
    btn.addEventListener('click', function () {
      var d = new Date();
      var stamp = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
        ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
      var head = '[' + stamp + '] ' + (f.getAttribute('data-user') || 'unbekannt') + ': ';
      ta.value = head + (ta.value ? '\n' + ta.value : '');
      ta.focus();
      ta.setSelectionRange(head.length, head.length);
    });
  });
  document.querySelectorAll('.guest-hostname').forEach(function (btn) {
    var box = btn.closest('.col-base').querySelector('.guest-info');
    function show(parts) {
      box.textContent = '';
      parts.forEach(function (p) {
        var el = document.createElement(p.pre ? 'pre' : 'div');
        el.textContent = p.text;
        box.appendChild(el);
      });
      box.hidden = false;
    }
    btn.addEventListener('click', function () {
      show([{ text: 'Lade Gast-Infos …' }]);
      fetch(btn.getAttribute('data-info-url'), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          var d = res.d, parts = [];
          if (!res.ok) { return show([{ text: d.error || 'Fehler beim Laden.' }]); }
          parts.push({ text: (d.type === 'qemu' ? 'VM' : 'LXC') + ' ' + d.vmid + ' · IP: ' + (d.ips.length ? d.ips.join(', ') : 'unbekannt') });
          if (d.agent_info) parts.push({ text: d.agent_info, pre: true });
          if (d.host_info) parts.push({ text: d.host_info, pre: true });
          if (d.note) parts.push({ text: d.note });
          show(parts);
        })
        .catch(function () { show([{ text: 'Gast-Infos konnten nicht geladen werden.' }]); });
    });
  });
})();
