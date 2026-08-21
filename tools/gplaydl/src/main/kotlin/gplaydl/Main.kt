package gplaydl

import java.nio.file.Path
import java.util.Locale

private const val DEFAULT_ANONYMOUS_DISPENSER_URL = "https://dispenser.gplaydl.com/api/auth"

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
    var userAgent = "com.aurora.store-4.8.4-76"
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

private fun anonymousDispenserUrls(): List<String> {
    val configured = System.getenv("GPLAY_ANON_DISPENSER_URLS")
        .orEmpty()
        .split(',', '\n')
        .map { it.trim() }
        .filter { it.isNotBlank() }
        .toMutableList()

    System.getenv("AURORA_DISPENSER_URL")
        .orEmpty()
        .trim()
        .takeIf { it.isNotBlank() }
        ?.let { configured += it }

    if (configured.isEmpty()) {
        configured += System.getenv("GPLAYDL_DISPENSER_URL")
            .orEmpty()
            .trim()
            .ifBlank { DEFAULT_ANONYMOUS_DISPENSER_URL }
    }
    return configured.distinct()
}

private fun authenticatedSession(options: CliOptions): Pair<GPlaySession, String> {
    val propertyCandidates = loadDevicePropertyCandidates(options.deviceProperties)
    val primaryProperties = propertyCandidates.first().second
    val email = System.getenv("GPLAY_EMAIL").orEmpty().trim()
    val aasToken = System.getenv("GPLAY_AAS_TOKEN").orEmpty().trim()
    val authToken = System.getenv("GPLAY_AUTH_TOKEN").orEmpty().trim()

    return when {
        email.isNotBlank() && aasToken.isNotBlank() ->
            GPlaySession.aas(email, aasToken, primaryProperties, options.locale) to "repository AAS credentials"

        email.isNotBlank() && authToken.isNotBlank() ->
            GPlaySession.authToken(email, authToken, primaryProperties, options.locale) to "repository AUTH credentials"

        else -> {
            val login = AnonymousAuthClient(
                dispenserUrls = anonymousDispenserUrls(),
                userAgent = options.userAgent,
                apiKey = System.getenv("GPLAYDL_API_KEY").orEmpty().trim().ifBlank { null },
            ).login(propertyCandidates)
            GPlaySession.anonymous(login.auth, login.properties, options.locale) to
                "anonymous dispenser ${login.dispenserHost} (${login.profileName})"
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
