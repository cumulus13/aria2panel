(function () {
  'use strict';
  var form = document.getElementById('server-form');
  var btn = document.getElementById('test-conn');
  var out = document.getElementById('test-result');
  btn.addEventListener('click', async function () {
    out.className = 'small ms-2 text-body-secondary';
    out.textContent = 'Testing…';
    try {
      var data = await Panel.post('/api/servers/test/', {
        server_id: form.dataset.serverId || null,
        rpc_url: form.elements['rpc_url'].value,
        secret: form.elements['secret'].value,
        verify_tls: form.elements['verify_tls'].checked
      });
      out.className = 'small ms-2 text-success';
      out.innerHTML = '<i class="bi bi-check-circle-fill"></i> Connected, aria2 ' + Panel.esc(data.version);
    } catch (e) {
      out.className = 'small ms-2 text-danger';
      out.innerHTML = '<i class="bi bi-x-circle-fill"></i> ' + Panel.esc(e.message);
    }
  });
})();
