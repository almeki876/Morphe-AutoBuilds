package gplaydl

import com.aurora.gplayapi.data.models.File as PlayFile
import com.aurora.gplayapi.helpers.AppDetailsHelper
import com.aurora.gplayapi.helpers.PurchaseHelper
import okhttp3.OkHttpClient
import okhttp3.Request
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption

class PlayDownloader(
    private val session: GPlaySession,
    private val client: OkHttpClient = OkHttpClient(),
) {
    data class Result(
        val packageName: String,
        val versionName: String,
        val versionCode: Long,
        val files: List<Path>,
    )

    fun download(
        packageName: String,
        outputDir: Path,
        requestedVersionCode: Long? = null,
    ): Result {
        val app = AppDetailsHelper(session.authData).getAppByPackageName(packageName)
        require(app.packageName == packageName) {
            "Google Play returned a different package: ${app.packageName}"
        }
        require(app.isFree) { "Paid apps are not supported by anonymous gplaydl" }

        // This pinned pure-JVM GPlayApi exposes Play versionCode as Int. Keep
        // the CLI/policy boundary as Long so callers never truncate silently,
        // then reject values the API cannot represent before purchase.
        val versionCode = requestedVersionCode ?: app.versionCode.toLong()
        require(versionCode in 0..Int.MAX_VALUE.toLong()) {
            "Google Play versionCode is outside the supported Int range: $versionCode"
        }
        val playFiles = PurchaseHelper(session.authData).purchase(
            packageName,
            versionCode.toInt(),
            app.offerType,
        )
        require(playFiles.isNotEmpty()) {
            "Google Play returned no downloadable files for $packageName@$versionCode"
        }

        Files.createDirectories(outputDir)
        val downloaded = playFiles.map { file ->
            downloadFile(file, outputDir)
        }
        return Result(
            packageName = packageName,
            versionName = app.versionName,
            versionCode = versionCode,
            files = downloaded,
        )
    }

    private fun downloadFile(file: PlayFile, outputDir: Path): Path {
        val safeName = file.name
            .ifBlank { if (file.type == PlayFile.FileType.BASE) "base.apk" else file.id }
            .substringAfterLast('/')
            .substringAfterLast('\\')
        val target = outputDir.resolve(safeName)
        val request = Request.Builder().url(file.url).get().build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                error("Google Play file download failed: HTTP ${response.code} for $safeName")
            }
            val body = response.body ?: error("Google Play returned an empty body for $safeName")
            body.byteStream().use { input ->
                Files.copy(input, target, StandardCopyOption.REPLACE_EXISTING)
            }
        }
        if (file.size > 0L && Files.size(target) != file.size) {
            Files.deleteIfExists(target)
            error("Google Play file size mismatch for $safeName")
        }
        return target
    }
}
