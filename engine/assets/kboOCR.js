/* Kindle Oasis Book Optimizer - trusted OCR helper for Adobe Acrobat Pro.
 *
 * Acrobat only allows OCR (doc.OCRPages) from a privileged context. Folder-level
 * scripts run privileged, so we expose one trusted function that the automation
 * layer can call from COM.
 *
 * Install to: <Acrobat>\Javascripts\kboOCR.js   (restart Acrobat afterwards)
 */
var kboOCR = app.trustedFunction(function (srcPath, dstPath, lang, lastPage) {
    app.beginPriv();
    try {
        var doc = app.openDoc({ cPath: srcPath });
        if (!doc) { return "OCR_ERR:cannot open " + srcPath; }
        try {
            doc.OCRLanguage = lang;
            doc.OCRDownsample = 600;
            doc.OCRType = "Searchable Image (Exact)";
        } catch (e) { /* older builds expose fewer knobs */ }
        doc.OCRPages(0, lastPage);
        doc.saveAs({ cPath: dstPath });
        doc.closeDoc(true);
        return "OCR_OK";
    } catch (e) {
        return "OCR_ERR:" + e;
    } finally {
        app.endPriv();
    }
});
