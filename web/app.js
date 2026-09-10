async function load(){
  const [topology, devices, events] = await Promise.all([fetch('/api/topology').then(r=>r.json()), fetch('/api/devices').then(r=>r.json()), fetch('/api/events').then(r=>r.json())]);
  document.querySelector('#topology').innerHTML = topology.devices.map(d=>`<article><b>${d.device_id}</b><small>${d.domain} · ${d.device_class}</small><small>${d.interfaces.map(i=>i.interface_id+':'+i.kind).join(' | ')}</small></article>`).join('');
  document.querySelector('#devices').innerHTML = devices.devices.map(s=>`<article class="${s.freshness}"><b>${s.device.device_id}</b><span>${s.freshness.toUpperCase()}</span><small>source: ${s.observed?.source||'none'} · simulation_only: ${s.device.simulation_only}</small><small>reconciliation: ${s.reconciliation.status}</small></article>`).join('');
  document.querySelector('#events').textContent = JSON.stringify(events.events, null, 2);
}
load().catch(e=>document.querySelector('#events').textContent='加载失败: '+e); setInterval(()=>load().catch(()=>{}), 3000);
