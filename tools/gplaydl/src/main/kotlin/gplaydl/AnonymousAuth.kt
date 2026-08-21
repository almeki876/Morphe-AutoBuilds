package gplaydl

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.Properties

@Serializable
data class DispenserAuth(
    val email: String,
    val authToken: String,
)

class AnonymousAuthClient(
    private val client: OkHttpClient = OkHttpClient(),
    private val dispenserUrl: String = "https://auroraoss.com/api/auth",
    private val userAgent: String = "com.aurora.store-4.8.4-76",
) {
    private val json = Json { ignoreUnknownKeys = true }

    fun login(properties: Properties): DispenserAuth {
        val payload = buildJsonObject {
            properties.stringPropertyNames().forEach { key ->
                put(key, properties.getProperty(key))
            }
        }
        val request = Request.Builder()
            .url(dispenserUrl)
            .header("User-Agent", userAgent)
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                error("Aurora dispenser failed: HTTP ${response.code}: $responseBody")
            }
            val auth = json.decodeFromString<DispenserAuth>(responseBody)
            require(auth.email.isNotBlank() && auth.authToken.isNotBlank()) {
                "Aurora dispenser returned incomplete credentials"
            }
            return auth
        }
    }
}
