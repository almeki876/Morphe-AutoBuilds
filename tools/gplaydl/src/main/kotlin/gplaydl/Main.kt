package gplaydl

import java.net.URI
import java.nio.file.Path
import java.util.Locale

private data class CliOptions(
    val command: String,
    val packages: List<String>,
    val output: Path,
    val versionCode: Long?,
    val deviceProperties: Path?,
    val userAgent: String,
    val locale: Locale,
)

private fun parseArgs(args: Array<String>): CliOptions {
    require(args.isNotEmpty()) {
        "usage: gplaydl <auth|download> [package ...] [--output DIR] [--version-code N] [--device-properties FILE] [--aurora-user-agent UA] [--locale TAG]"
    }
    val command = args[0]
    require(command == "auth" || command == "download") { "unknown command: $command" }

    var output = Path.of(".")
    var versionCode: Long? = null
    var deviceProperties: Path? = null
    var userAgent = "Morphe-AutoBuilds-gplaydl/1.0"
    var locale = Locale.JAPAN
    val packages = mutableListOf<String>()

    var index = 1
    while (index < args.size) {
        when (val arg = args[index]) {
            "--output" -> output = Path.of(args.getOrNull(++index) ?: error("--output requires a value"))
            "--version-code" -> versionCode = (args.getOrNull(++index) ?: error("--version-code requires a value")).toLong()
            "--device-properties" -> deviceProperties = Path.of(args.getOrNull(++index) ?: error("--device-properties requires a value"))
            "--aurora-user-agent" -> userAgent = args.getOrNull(++index) ?: error("--aurora-user-agent requires a value")
            "--locale" -> locale = Locale.forLanguageTag(args.getOrNull(++index) ?: error("--locale requires a value"))
            else -> {
                require(!arg.startsWith("--")) { "unknown option: $arg" }
                packages += arg
            }
        }
        index++
    }

    if (command == "download") require(packages.isNotEmpty()) { "download requires at least one package" }
    if (packages.size > 1 && versionCode != null) {
        error("--version-code can only be used when downloading one package")
    }
    return CliOptions(command, packages, output, versionCode, deviceProperties, userAgent, locale)
}

private fun configuredDispenserUrls(): List<String> {
    // GPLAY_DISPENSER_URLS is the preferred multi-endpoint setting. Keep the
    // singular form and the old AURORA_DISPENSER_URL name for compatibility.
    return sequenceOf(
        System.getenv("GPLAY_DISPENSER_URLS"),
        System.getenv("GPLAY_DISPENSER_URL"),
        System.getenv("AURORA_DISPENSER_URL"),
    )
        .flatMap { value -> value.orEmpty().split(',', ';', '\n').asSequence() }
        .map { it.trim() }
        .filter { it.isNotBlank() }
        .distinct()
        .toList()
}

private fun safeDispenserHost(url: String): String =
    runCatching { URI(url).host }.getOrNull()
        ?.takeIf { it.isNotBlank() }
        ?: "configured dispenser"

private fun authenticatedSession(options: CliOptions): Pair<GPlaySession, String> {
    val properties = loadDeviceProperties(options.deviceProperties)
    val email = System.getenv("GPLAY_EMAIL").orEmpty().trim()
    val aasToken = System.getenv("GPLAY_AAS_TOKEN").orEmpty().trim()
    val authToken = System.getenv("GPLAY_AUTH_TOKEN").orEmpty().trim()
    val dispenserApiKey = System.getenv("GPLAYDL_API_KEY").orEmpty().trim().ifBlank { null }

    return when {
        email.isNotBlank() && aasToken.isNotBlank() ->
            GPlaySession.aas(email, aasToken, properties, options.locale) to "repository AAS credentials"

        email.isNotBlank() && authToken.isNotBlank() ->
            GPlaySession.authToken(email, authToken, properties, options.locale) to "repository AUTH credentials"

        else -> {
            val dispenserUrls = configuredDispenserUrls()
            require(dispenserUrls.isNotEmpty()) {
                "anonymous Google Play authentication requires GPLAY_DISPENSER_URLS or GPLAY_DISPENSER_URL; " +
                    "no third-party dispenser is hardcoded"
            }

            val failures = mutableListOf<String>()
            for (dispenserUrl in dispenserUrls) {
                try {
                    val dispenserAuth = AnonymousAuthClient(
                        dispenserUrl = dispenserUrl,
                        apiKey = dispenserApiKey,
                        userAgent = options.userAgent,
                    ).login(properties)
                    return GPlaySession.anonymous(dispenserAuth, properties, options.locale) to
                        "configured anonymous dispenser (${safeDispenserHost(dispenserUrl)})"
                } catch (error: Exception) {
                    val detail = error.message.orEmpty().lineSequence().firstOrNull().orEmpty().take(180)
                    failures += "${safeDispenserHost(dispenserUrl)}: ${error::class.simpleName}" +
                        if (detail.isNotBlank()) " ($detail)" else ""
                }
            }
            error("all configured Google Play token dispensers failed: ${failures.joinToString("; ")}")
        }
    }
}

fun main(args: Array<String>) {
    val options = parseArgs(args)
    val (session, authSource) = authenticatedSession(options)

    if (options.command == "auth") {
        println("Google Play login OK via $authSource")
        return
    }

    // Build one authenticated session and reuse it for every package requested
    // by this process. Never print or persist any authentication token.
    val downloader = PlayDownloader(session)
    for (packageName in options.packages) {
        val packageOutput = options.output.resolve(packageName)
        val result = downloader.download(
            packageName = packageName,
            outputDir = packageOutput,
            requestedVersionCode = options.versionCode,
        )
        println(
            "Downloaded ${result.packageName} versionName=${result.versionName} " +
                "versionCode=${result.versionCode} files=${result.files.size} source=GooglePlay"
        )
    }
}
