// Show/hide toggle for password fields.
//
// Progressive enhancement: without JavaScript the fields simply stay hidden.
// The button labels come from data attributes on the <script> tag so they are
// translated by the server like the rest of the interface.
(function () {
  var script = document.querySelector("script[data-show-label]");
  var labels = {
    show: script ? script.dataset.showLabel : "Show",
    hide: script ? script.dataset.hideLabel : "Hide",
    aria: script ? script.dataset.ariaLabel : "Show password",
  };

  function reveal(input, button, visible) {
    input.type = visible ? "text" : "password";
    button.textContent = visible ? labels.hide : labels.show;
    button.setAttribute("aria-pressed", visible ? "true" : "false");
  }

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    var wrapper = document.createElement("span");
    wrapper.className = "password-field";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    var button = document.createElement("button");
    button.type = "button"; // never submits the form
    button.className = "password-toggle";
    button.setAttribute("aria-label", labels.aria);
    wrapper.appendChild(button);
    reveal(input, button, false);

    button.addEventListener("click", function () {
      reveal(input, button, input.type === "password");
    });

    // Hide it again when the form is sent, and when the page is left and restored from the
    // back/forward cache, so a password is never left on screen.
    if (input.form) {
      input.form.addEventListener("submit", function () { reveal(input, button, false); });
    }
    window.addEventListener("pagehide", function () { reveal(input, button, false); });
  });
})();
