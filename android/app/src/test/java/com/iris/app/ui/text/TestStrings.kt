package com.iris.app.ui.text

import com.iris.app.R
import java.io.File
import java.util.Locale
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.Element

/**
 * Lets plain JVM tests read texts from the real resource files (Portuguese by default,
 * or another values folder) through [appString] and [appPlural].
 */
object TestStrings {
    private val resFolder: File = listOf("src/main/res", "app/src/main/res").map(::File).first { it.isDirectory }

    fun install(values: String = "values") {
        val strings = HashMap<String, String>()
        val plurals = HashMap<String, Map<String, String>>()
        val arrays = HashMap<String, Array<String>>()
        // The default values come first so a locale folder only overrides what it translates.
        listOf("values", values).distinct().forEach { folder ->
            val file = File(resFolder, "$folder/strings.xml")
            if (!file.exists()) return@forEach
            val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file)
            val root = doc.documentElement
            for (i in 0 until root.childNodes.length) {
                val node = root.childNodes.item(i) as? Element ?: continue
                val name = node.getAttribute("name")
                when (node.tagName) {
                    "string" -> strings[name] = unescape(node.textContent)
                    "string-array" -> arrays[name] = (0 until node.childNodes.length)
                        .mapNotNull { node.childNodes.item(it) as? Element }
                        .map { unescape(it.textContent) }
                        .toTypedArray()
                    "plurals" -> plurals[name] = (0 until node.childNodes.length)
                        .mapNotNull { node.childNodes.item(it) as? Element }
                        .associate { it.getAttribute("quantity") to unescape(it.textContent) }
                }
            }
        }
        val stringIds = R.string::class.java.fields.associate { it.getInt(null) to it.name }
        val pluralIds = R.plurals::class.java.fields.associate { it.getInt(null) to it.name }
        val arrayIds = R.array::class.java.fields.associate { it.getInt(null) to it.name }
        AppStrings.resolver = object : Resolver {
            override fun stringArray(id: Int) = arrays.getValue(arrayIds.getValue(id))

            override fun string(id: Int, args: Array<out Any>) =
                String.format(Locale.ROOT, strings.getValue(stringIds.getValue(id)), *args)

            override fun plural(id: Int, count: Int, args: Array<out Any>): String {
                val items = plurals.getValue(pluralIds.getValue(id))
                // Portuguese CLDR rule, as Android applies it to values/: 0 and 1 are "one".
                val one = count == 0 || count == 1
                val text = if (one) items["one"] ?: items.getValue("other") else items.getValue("other")
                return String.format(Locale.ROOT, text, *args)
            }
        }
    }

    fun uninstall() {
        AppStrings.resolver = null
    }

    private fun unescape(raw: String) = raw.replace("\\'", "'").replace("\\\"", "\"").replace("\\n", "\n").replace("\\@", "@").replace("\\?", "?")
}
