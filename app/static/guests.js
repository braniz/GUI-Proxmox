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
})();
