package com.iris.app.ui.text

/** "1 item", "2 itens", "0 itens": a count with the word in the right number. */
fun countOf(count: Int, singular: String, plural: String): String =
    "$count ${if (count == 1) singular else plural}"
