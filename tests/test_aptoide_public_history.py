import unittest

from src import aptoide


class AptoidePublicHistoryTests(unittest.TestCase):
    def test_structured_frontend_json_resolves_exact_release(self) -> None:
        html = b"""
        <html><script type="application/json">
        {
          "app": {
            "package": "com.adobe.reader",
            "file": {
              "vername": "26.7.1.47181",
              "path": "https://pool.apk.aptoide.com/example/com-adobe-reader-26-7-1.apk"
            }
          }
        }
        </script></html>
        """
        self.assertEqual(
            aptoide._public_html_exact_path(
                html, "com.adobe.reader", "26.7.1.47181"
            ),
            "https://pool.apk.aptoide.com/example/com-adobe-reader-26-7-1.apk",
        )

    def test_js_assignment_fallback_stays_bound_to_exact_version(self) -> None:
        html = r'''
        <script>
        window.__APP__ = {"package":"com.adobe.reader","releases":[
          {"vername":"26.7.2.47372","path":"https:\/\/pool.apk.aptoide.com\/reader-new.apk"},
          {"vername":"26.7.1.47181","path":"https:\/\/pool.apk.aptoide.com\/reader-target.apk"}
        ]};
        </script>
        '''
        self.assertEqual(
            aptoide._public_html_exact_path(
                html, "com.adobe.reader", "26.7.1.47181"
            ),
            "https://pool.apk.aptoide.com/reader-target.apk",
        )

    def test_public_metadata_rejects_external_download_host(self) -> None:
        html = b"""
        <script type="application/json">
        {"package":"com.adobe.reader","file":{
          "vername":"26.7.1.47181",
          "path":"https://example.invalid/reader.apk"
        }}
        </script>
        """
        self.assertIsNone(
            aptoide._public_html_exact_path(
                html, "com.adobe.reader", "26.7.1.47181"
            )
        )

    def test_public_metadata_rejects_other_version(self) -> None:
        html = b"""
        <script type="application/json">
        {"package":"com.adobe.reader","file":{
          "vername":"26.7.2.47372",
          "path":"https://pool.apk.aptoide.com/reader-new.apk"
        }}
        </script>
        """
        self.assertIsNone(
            aptoide._public_html_exact_path(
                html, "com.adobe.reader", "26.7.1.47181"
            )
        )


if __name__ == "__main__":
    unittest.main()
