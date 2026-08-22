import unittest
from unittest import mock

from src import aptoide


class AptoideRegionalFallbackTests(unittest.TestCase):
    def test_regional_exact_path_requires_package_and_version_match(self) -> None:
        wrong = mock.Mock(status_code=200)
        wrong.json.return_value = {
            "nodes": {
                "meta": {
                    "data": {
                        "package": "com.adobe.reader",
                        "file": {
                            "vername": "26.7.0.47169",
                            "path": "https://pool.apk.aptoide.com/wrong.apk",
                        },
                    }
                }
            }
        }
        exact = mock.Mock(status_code=200)
        exact.json.return_value = {
            "nodes": {
                "meta": {
                    "data": {
                        "package": "com.adobe.reader",
                        "file": {
                            "vername": "26.7.1.47181",
                            "path": "https://pool.apk.aptoide.com/exact.apk",
                        },
                    }
                }
            }
        }

        with mock.patch(
            "src.aptoide.utils.cf_aware_get", side_effect=[wrong, exact]
        ) as get:
            path = aptoide._regional_exact_path(
                "com.adobe.reader",
                "26.7.1.47181",
                "",
                ["br", "bd"],
            )

        self.assertEqual(path, "https://pool.apk.aptoide.com/exact.apk")
        self.assertEqual(get.call_count, 2)
        self.assertIn("country=br", get.call_args_list[0].args[0])
        self.assertIn("country=bd", get.call_args_list[1].args[0])

    def test_regional_exact_path_rejects_wrong_package(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "nodes": {
                "meta": {
                    "data": {
                        "package": "com.example.other",
                        "file": {
                            "vername": "26.7.1.47181",
                            "path": "https://pool.apk.aptoide.com/wrong.apk",
                        },
                    }
                }
            }
        }
        with mock.patch("src.aptoide.utils.cf_aware_get", return_value=response):
            path = aptoide._regional_exact_path(
                "com.adobe.reader",
                "26.7.1.47181",
                "",
                ["br"],
            )
        self.assertIsNone(path)

    def test_regional_exact_path_bounds_and_validates_country_codes(self) -> None:
        response = mock.Mock(status_code=404)
        with mock.patch(
            "src.aptoide.utils.cf_aware_get", return_value=response
        ) as get:
            path = aptoide._regional_exact_path(
                "com.adobe.reader",
                "26.7.1.47181",
                "",
                ["BR", "br", "bad", "bd"],
            )
        self.assertIsNone(path)
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
