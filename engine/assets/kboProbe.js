/* Probe: enumerate document/app members in a privileged context so we can find
 * whatever the current Acrobat build calls its OCR entry point. */
var kboProbe = app.trustedFunction(function (srcPath) {
    app.beginPriv();
    var out = [];
    try {
        var doc = app.openDoc({ cPath: srcPath });
        for (var k in doc) {
            try {
                if (/ocr|recog|scan|enhance|text/i.test(k)) {
                    out.push("doc." + k + "=" + (typeof doc[k]));
                }
            } catch (e) { }
        }
        out.push("=== app ===");
        for (var k2 in app) {
            try {
                if (/ocr|recog|scan|enhance/i.test(k2)) {
                    out.push("app." + k2 + "=" + (typeof app[k2]));
                }
            } catch (e) { }
        }
        out.push("=== global ===");
        for (var k3 in this) {
            try {
                if (/ocr|recog/i.test(k3)) {
                    out.push("g." + k3 + "=" + (typeof this[k3]));
                }
            } catch (e) { }
        }
        out.push("viewerVersion=" + app.viewerVersion);
        out.push("viewerType=" + app.viewerType);
        doc.closeDoc(true);
    } catch (e) {
        out.push("ERR:" + e);
    } finally {
        app.endPriv();
    }
    return out.join("|");
});
