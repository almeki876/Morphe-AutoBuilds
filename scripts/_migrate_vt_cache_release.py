from pathlib import Path


workflow = Path('.github/workflows/build.yml')
text = workflow.read_text(encoding='utf-8')
needle = '''          find ./release-apks -name '*.apk' -print0 \\
            | xargs -0 -r -P 8 -I {} gh release upload "$release_tag" "{}"

          echo "Release created and APK assets uploaded successfully!"
'''
replacement = '''          find ./release-apks -name '*.apk' -print0 \\
            | xargs -0 -r -P 8 -I {} gh release upload "$release_tag" "{}"

          python3 scripts/export_virustotal_cache.py \\
            virustotal_base_results.json \\
            virustotal-cache-v1.json
          test -s virustotal-cache-v1.json
          gh release upload "$release_tag" virustotal-cache-v1.json \\
            --repo "${{ github.repository }}" \\
            --clobber
          echo "VirusTotal SHA cache attached to $release_tag"

          echo "Release created and APK assets uploaded successfully!"
'''
count = text.count(needle)
if count != 1:
    raise SystemExit(f'expected one Release upload insertion point, found {count}')
workflow.write_text(text.replace(needle, replacement), encoding='utf-8')
