// Decks page: delete one deck from its ⋮ menu, select several to delete, and
// confirm "Delete for good" in Recently deleted. Every delete asks first.
(function () {
  "use strict";
  const dialog = document.getElementById("confirm-dialog");

  function confirmBox(title, text, okLabel) {
    // Falls back to the browser's confirm() where <dialog> isn't supported.
    if (!dialog || typeof dialog.showModal !== "function") return Promise.resolve(window.confirm(text));
    dialog.querySelector("#confirm-title").textContent = title;
    dialog.querySelector("[data-confirm-text]").textContent = text;
    dialog.querySelector("[data-confirm-ok]").textContent = okLabel;
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    });
  }

  // One deck, from its ⋮ menu.
  const single = document.getElementById("single-delete");
  document.querySelectorAll("[data-delete-url]").forEach((link) => {
    link.addEventListener("click", async (event) => {
      event.preventDefault();
      link.closest("details")?.removeAttribute("open");
      const name = link.dataset.deckName;
      const ok = await confirmBox("Delete deck?",
        `Delete "${name}"? Your cards stay in your library, and you can put the deck back from Recently deleted for 30 days.`,
        "Delete deck");
      if (!ok) return;
      single.action = link.dataset.deleteUrl;
      single.submit();
    });
  });

  // Several decks at once.
  const bulk = document.getElementById("bulk-delete");
  if (bulk) {
    const toggle = bulk.querySelector("[data-select-toggle]");
    const countEl = bulk.querySelector("[data-select-count]");
    const delBtn = bulk.querySelector("[data-delete-selected]");
    const allBtn = bulk.querySelector("[data-select-all]");
    const boxes = () => [...document.querySelectorAll('.ad-pick input[form="bulk-delete"]')];
    const picked = () => boxes().filter((b) => b.checked);

    function update() {
      const n = picked().length;
      countEl.textContent = `${n} selected`;
      delBtn.disabled = n === 0;
      delBtn.textContent = n ? `Delete ${n} deck${n === 1 ? "" : "s"}` : "Delete selected";
      allBtn.textContent = n && n === boxes().length ? "Clear" : "Select all";
      boxes().forEach((b) => b.closest(".ad-tile").classList.toggle("picked", b.checked));
    }
    function setMode(on) {
      document.body.classList.toggle("selecting-decks", on);
      toggle.setAttribute("aria-pressed", String(on));
      toggle.textContent = on ? "Done" : "Select decks";
      if (!on) boxes().forEach((b) => { b.checked = false; });
      update();
    }
    toggle.addEventListener("click", () => setMode(!document.body.classList.contains("selecting-decks")));
    allBtn.addEventListener("click", () => {
      const all = picked().length !== boxes().length;
      boxes().forEach((b) => { b.checked = all; });
      update();
    });
    boxes().forEach((b) => b.addEventListener("change", update));
    // In select mode, clicking a tile's cover ticks it instead of opening the deck.
    document.querySelectorAll(".ad-tile .ad-cover").forEach((cover) => {
      cover.addEventListener("click", (event) => {
        if (!document.body.classList.contains("selecting-decks")) return;
        event.preventDefault();
        const box = cover.closest(".ad-tile").querySelector(".ad-pick input");
        box.checked = !box.checked;
        update();
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && document.body.classList.contains("selecting-decks") && !dialog?.open) setMode(false);
    });
    bulk.addEventListener("submit", async (event) => {
      if (bulk.dataset.confirmed === "yes") return;
      event.preventDefault();
      const names = picked().map((b) => b.closest(".ad-tile").querySelector(".ad-name").textContent.trim());
      if (!names.length) return;
      const list = names.length > 6 ? names.slice(0, 6).join(", ") + ` and ${names.length - 6} more` : names.join(", ");
      const ok = await confirmBox(`Delete ${names.length} deck${names.length === 1 ? "" : "s"}?`,
        `Delete ${list}? Your cards stay in your library, and you can put these decks back from Recently deleted for 30 days.`,
        `Delete ${names.length} deck${names.length === 1 ? "" : "s"}`);
      if (!ok) return;
      bulk.querySelector('input[name="confirm"]').value = "yes";
      bulk.dataset.confirmed = "yes";
      bulk.submit();
    });
    update();
  }

  // "Delete for good" in Recently deleted.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (form.dataset.confirmed === "yes") return;
      event.preventDefault();
      const ok = await confirmBox("Delete for good?", form.dataset.confirm, "Delete for good");
      if (!ok) return;
      form.querySelector('input[name="confirm"]').value = "yes";
      form.dataset.confirmed = "yes";
      form.submit();
    });
  });
})();
