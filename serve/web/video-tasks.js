// The browser stores only an owner token. Originals and checkpoints stay local
// to Strata, expire after 24 hours and can be deleted from this page.
(function(root) {
  'use strict';
  let owner;
  function token() {
    if(owner)return owner;
    const saved=localStorage.getItem('strata.video_owner');
    if(/^[a-f0-9]{64}$/.test(saved||''))return owner=saved;
    owner=Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,'0')).join('');
    localStorage.setItem('strata.video_owner',owner);return owner;
  }
  async function call(path,value,headers,signal) {
    const response=await fetch(path,{method:'POST',headers:headers(true),signal,
      body:JSON.stringify({...value,owner:token()})});
    const result=await response.json();
    if(!response.ok || result.error)throw new Error(result.error?.message||`HTTP ${response.status}`);
    return result;
  }
  const api={token,call};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.StrataVideoTasks=api;
})(globalThis);
