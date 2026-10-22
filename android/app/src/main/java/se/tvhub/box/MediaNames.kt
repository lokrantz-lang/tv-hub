package se.tvhub.box

/** Filnamn till läsbara titlar och naturlig sortering (ren Kotlin, testas på datorn). */
object MediaNames {
    val VIDEO = setOf("mkv", "mp4", "m4v", "avi", "mov", "ts", "m2ts", "mts", "webm", "mpg", "mpeg", "wmv", "flv", "3gp", "vob")
    val IMAGE = setOf("jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp")

    private val TAGS = Regex(
        "(?i)\\b(2160p|1080p|1080i|720p|480p|4k|uhd|hdr10?|hdr|dv|bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|web|hdtv|dvdrip|" +
            "x264|x265|h264|h265|hevc|avc|aac|ac3|eac3|dts|truehd|atmos|ddp?5\\.1|5\\.1|7\\.1|remux|proper|repack|extended|" +
            "multi|swesub|nordic|sv|eng|sub|subs)\\b.*$",
    )

    /** "The.Matrix.1999.1080p.BluRay.x264.mkv" -> "The Matrix (1999)". "Show.S01E02.720p" -> "Show S01E02". */
    fun cleanTitle(raw: String): String {
        var s = raw.substringBeforeLast('.', raw).let { if (raw.substringAfterLast('.', "").lowercase() in VIDEO + IMAGE) it else raw }
        s = s.replace('_', ' ').replace(Regex("(?<=\\S)\\.(?=\\S)"), " ").replace(Regex("\\[((?:19|20)\\d{2})]"), " $1 ").replace(Regex("\\[[^]]*]"), " ").trim()
        Regex("(?i)^(.*?)\\b(s\\d{1,2}\\s?e\\d{1,3})\\b").find(s)?.let { m ->
            val show = m.groupValues[1].trim(' ', '-')
            return listOf(show, m.groupValues[2].uppercase().replace(" ", "")).filter { it.isNotEmpty() }.joinToString(" ")
        }
        Regex("^(.*?)[\\s(]+((?:19|20)\\d{2})\\)?(\\s.*)?$").find(s)?.let { m ->
            val title = m.groupValues[1].trim(' ', '-', '(')
            if (title.isNotEmpty()) return "$title (${m.groupValues[2]})"
        }
        s = s.replace(TAGS, "").trim(' ', '-')
        return s.ifEmpty { raw }
    }

    /** Naturlig sortering: "Avsnitt 2" före "Avsnitt 10". */
    val naturalOrder = Comparator<String> { a, b ->
        val re = Regex("(\\d+)|(\\D+)")
        val pa = re.findAll(a.lowercase()).map { it.value }.toList()
        val pb = re.findAll(b.lowercase()).map { it.value }.toList()
        for (i in 0 until minOf(pa.size, pb.size)) {
            val x = pa[i]
            val y = pb[i]
            val c = if (x[0].isDigit() && y[0].isDigit()) x.toBigInteger().compareTo(y.toBigInteger()) else x.compareTo(y)
            if (c != 0) return@Comparator c
        }
        pa.size - pb.size
    }


    private val NAME_DATE = Regex("(?<!\\d)((?:19|20)\\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\\d|3[01])(?!\\d)")

    /** Datum ur filnamn från kameror och mobiler: VID_20190612_…, IMG_20190612…, PXL_…, "2019-06-12 Midsommar". */
    fun dateFromName(name: String): Triple<Int, Int, Int>? {
        val m = NAME_DATE.find(name) ?: return null
        val y = m.groupValues[1].toInt()
        if (y < 1990 || y > 2100) return null
        return Triple(y, m.groupValues[2].toInt(), m.groupValues[3].toInt())
    }
}
