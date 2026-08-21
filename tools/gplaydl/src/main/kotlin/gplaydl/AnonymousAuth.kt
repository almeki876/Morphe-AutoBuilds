package gplaydl

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URI
import java.util.Properties

data class DispenserAuth(
    val email: String,
    val authToken: String,
)

data class AnonymousLogin(
    val auth: DispenserAuth,
    val properties: Properties,
    val profileName: String,
    val dispenserHost: String,
)

class AnonymousAuthClient(
    private val dispenserUrls: List<String>,
    private val client: OkHttpClient = OkHttpClient(),
    private val userAgent: String = "com.aurora.store-4.8.4-76",
    private val apiKey: String? = null,
) {
    private val json = Json { ignoreUnknownKeys = true }

    fun login(propertyCandidates: List<Pair<String, Properties>>): AnonymousLogin {
        require(dispenserUrls.isNotEmpty()) { "at least one anonymous dispenser URL is required" }
        require(propertyCandidates.isNotEmpty()) { "at least one Android device profile is required" }

        val failures = linkedSetOf<String>()
        for (dispenserUrl in dispenserUrls.distinct()) {
            require(dispenserUrl.isNotBlank()) { "anonymous dispenser URL must not be blank" }
            val host = runCatching { URI(dispenserUrl).host }.getOrNull()
                ?.takeIf { it.isNotBlank() }
                ?: "configured dispenser"

            for ((profileName, properties) in propertyCandidates) {
                val payload = buildJsonObject {
                    properties.stringPropertyNames().forEach { key ->
                        put(key, properties.getProperty(key))
                    }
                }
                val requestBuilder = Request.Builder()
                    .url(dispenserUrl)
                    .header("User-Agent", userAgent)
                    .post(payload.toString().toRequestBody("application/json".toMediaType()))
                apiKey?.trim()?.takeIf { it.isNotBlank() }?.let {
                    requestBuilder.header("X-Api-Key", it)
                }

                try {
                    client.newCall(requestBuilder.build()).execute().use { response ->
                        val responseBody = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            failures += "$host HTTP ${response.code}"
                            // 401/403 are caller/server policy failures. A different device
                            // profile cannot repair them, so move to the next dispenser.
                            if (response.code == 401 || response.code == 403) break
                            continue
                        }

                        val objectValue = json.parseToJsonElement(responseBody).jsonObject
                        val email = objectValue["email"]?.jsonPrimitive?.content.orEmpty()
                        val authToken = objectValue["authToken"]?.jsonPrimitive?.content.orEmpty()
                        if (email.isNotBlank() && authToken.isNotBlank()) {
                            return AnonymousLogin(
                                auth = DispenserAuth(email = email, authToken = authToken),
                                properties = properties,
                                profileName = profileName,
                                dispenserHost = host,
                            )
                        }
                        failures += "$host incomplete credentials"
                    }
                } catch (exception: Exception) {
                    failures += "$host ${exception::class.simpleName ?: "request failure"}"
                }
            }
        }

        error(
            "Anonymous Google Play authentication failed: " +
                failures.take(8).joinToString("; ").ifBlank { "no dispenser accepted a device profile" }
        )
    }
}
