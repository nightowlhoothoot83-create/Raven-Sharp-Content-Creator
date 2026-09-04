(() => {
  if (document.getElementById('adg-group-footer')) return;
  const brands = [
    ['MyCalendarTools','https://mycalendartools.net/assets/perf/mycalendartools-logo.webp','https://mycalendartools.net'],
    ['MyCalcTools','https://mycalendartools.net/assets/perf/mycalctools-logo.webp','https://mycalctools.net'],
    ['Wheel Name Picker','https://mycalendartools.net/assets/perf/wheelnamepicker-logo.webp','https://wheelnamepicker.com.au'],
    ['Raven Sharp','https://mycalendartools.net/assets/perf/raven-sharp.webp','https://raven-sharp.com'],
    ['Zyia Creations','https://mycalendartools.net/assets/perf/zyia-creations.webp','https://zyia-creations.printify.me/'],
    ['ADG Downloads','https://mycalendartools.net/assets/perf/adg-downloads.webp','https://zyiacreations.etsy.com'],
    ['Feed the Feed','https://mycalendartools.net/assets/perf/feed-the-feed.webp','https://www.facebook.com/share/1HfyRTQtg1/'],
    ['Spew Crew Kids','https://mycalendartools.net/assets/perf/spew-crew.webp','https://www.youtube.com/@spewcrewkids'],
    ['Mystical Moments','https://mycalendartools.net/assets/perf/mystical-moments.webp','https://mysticalmoments.pages.dev']
  ];
  const style = document.createElement('style');
  style.id='adg-footer-styles';
  style.textContent=`#adg-group-footer{background:#080810;border-top:1px solid rgba(255,255,255,.08);padding:48px 24px;text-align:center;font-family:Outfit,Arial,sans-serif;color:#eeeaf8}#adg-group-footer *{box-sizing:border-box}#adg-group-footer .adg-logo{width:220px;max-width:75vw;height:auto;border-radius:12px;display:block;margin:0 auto 20px;filter:drop-shadow(0 0 16px rgba(6,214,255,.3))}#adg-group-footer h3{margin:0 0 6px;font-size:20px;color:#fff}#adg-group-footer .tag{margin:0 0 24px;color:#a0a0c0;font-size:14px}#adg-group-footer .brands{display:flex;justify-content:center;align-items:center;gap:16px;flex-wrap:wrap;margin:0 auto 26px;max-width:720px}#adg-group-footer .brands a{opacity:.85;transition:.2s}#adg-group-footer .brands a:hover{opacity:1;transform:translateY(-2px)}#adg-group-footer .brands img{width:52px;height:52px;border-radius:12px;object-fit:cover;display:block}#adg-group-footer .support{display:inline-flex;padding:10px 22px;border-radius:999px;background:linear-gradient(135deg,#06d6ff,#8b5cf6);box-shadow:0 0 20px rgba(6,214,255,.3),0 0 40px rgba(139,92,246,.2);color:#fff;text-decoration:none;font-weight:700}`;
  document.head.appendChild(style);
  const footer=document.createElement('footer');
  footer.id='adg-group-footer';
  footer.innerHTML=`<a href="https://ascensiondigitalgroup.com" target="_blank" rel="noopener"><img class="adg-logo" src="https://mycalendartools.net/assets/perf/ascension-digital.webp" alt="Ascension Digital Group"></a><h3>Part of the Ascension Digital Group</h3><p class="tag">Elevating Your Digital Future</p><div class="brands">${brands.map(([n,i,u])=>`<a href="${u}" target="_blank" rel="noopener" title="${n}"><img src="${i}" alt="${n}" loading="lazy"></a>`).join('')}</div><a class="support" href="/about/#support">Support Us</a>`;
  document.body.appendChild(footer);
})();
