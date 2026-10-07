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
  if (saved) setAll(true);
})();
