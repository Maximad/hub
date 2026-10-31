// Keep a real text fallback visible if approved media disappears or fails to load.
document.querySelectorAll('.public-home .ph-art img').forEach((image) => {
  const fallback = () => { image.hidden = true; };
  image.addEventListener('error', fallback);
  if (image.complete && image.naturalWidth === 0) fallback();
});
