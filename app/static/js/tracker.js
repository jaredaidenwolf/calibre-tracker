// tracker.js — tracker-specific UI glue loaded after caliBlur.js.
//
// Tooltip init for action-icon-buttons. caliBlur.js does two things
// that defeat the obvious `$('[data-toggle="tooltip"]').tooltip()`
// path for our icon row:
//
//   1. It strips `data-toggle` off every <a:not(.dropdown-toggle)> at
//      load time (caliBlur.js:284), so our anchor icons would lose the
//      attribute before any tooltip-init selector could find them.
//   2. Its global tooltip init runs with default `animation: true` and
//      Bootstrap's default delay, which feels sluggish.
//
// So we select by the `.action-icon-btn` class instead (immune to step
// 1) and re-init with zero delay + no animation. `tooltip('destroy')`
// first is a no-op for the anchors (caliBlur never bound them) and
// safely clears the existing binding on the delete <button> before we
// replace it.
$(function () {
    var $tooltips = $('.action-icon-btn');
    if ($tooltips.length) {
        $tooltips.tooltip('destroy').tooltip({
            container: 'body',
            trigger: 'hover focus',
            animation: false,
            delay: { show: 0, hide: 0 }
        });
    }
});
