package com.ipsco.vcamagent

import java.io.OutputStream

/**
 * Minimal MPEG-TS muxer for one H.264 stream (what the VCam agent hub expects: the server side
 * demuxes it with GStreamer tsdemux). PAT/PMT before every key frame, PCR on the video PID.
 */
class TsMuxer(private val out: OutputStream) {
    private val videoPid = 0x100
    private val pmtPid = 0x1000
    private val cc = IntArray(0x2000)
    private val pkt = ByteArray(188)

    fun writeFrame(annexB: ByteArray, pts90k: Long, key: Boolean) {
        if (key) {
            writeSection(0, pat())
            writeSection(pmtPid, pmt())
        }
        val aud = byteArrayOf(0, 0, 0, 1, 0x09, 0xF0.toByte())
        val pes = ByteArray(14 + aud.size + annexB.size)
        pes[0] = 0; pes[1] = 0; pes[2] = 1; pes[3] = 0xE0.toByte()
        pes[4] = 0; pes[5] = 0                 // unbounded (allowed for video)
        pes[6] = 0x80.toByte(); pes[7] = 0x80.toByte(); pes[8] = 5   // PTS only
        writePts(pes, 9, pts90k)
        System.arraycopy(aud, 0, pes, 14, aud.size)
        System.arraycopy(annexB, 0, pes, 14 + aud.size, annexB.size)
        val pcr = if (pts90k > 9000) pts90k - 9000 else 0
        packetize(videoPid, pes, pcr, key)
        out.flush()
    }

    private fun packetize(pid: Int, payload: ByteArray, pcr: Long?, rai: Boolean) {
        var pos = 0
        var first = true
        while (pos < payload.size) {
            java.util.Arrays.fill(pkt, 0xFF.toByte())
            val remaining = payload.size - pos
            val withPcr = first && pcr != null
            var af = if (withPcr) 8 else 0          // adaptation field incl. its length byte
            var space = 184 - af
            if (remaining < space) {                 // last packet: stuff the adaptation field
                af += space - remaining
                space = remaining
            }
            pkt[0] = 0x47
            pkt[1] = ((if (first) 0x40 else 0) or ((pid shr 8) and 0x1F)).toByte()
            pkt[2] = (pid and 0xFF).toByte()
            pkt[3] = ((if (af > 0) 0x30 else 0x10) or (cc[pid] and 0x0F)).toByte()
            cc[pid] = (cc[pid] + 1) and 0x0F
            if (af > 0) {
                pkt[4] = (af - 1).toByte()
                if (af > 1) {
                    pkt[5] = ((if (withPcr) 0x10 else 0) or (if (first && rai) 0x40 else 0)).toByte()
                    if (withPcr) writePcr(pkt, 6, pcr!!)
                }
            }
            System.arraycopy(payload, pos, pkt, 4 + af, space)
            pos += space
            first = false
            out.write(pkt)
        }
    }

    private fun writeSection(pid: Int, section: ByteArray) {
        java.util.Arrays.fill(pkt, 0xFF.toByte())
        pkt[0] = 0x47
        pkt[1] = (0x40 or ((pid shr 8) and 0x1F)).toByte()
        pkt[2] = (pid and 0xFF).toByte()
        pkt[3] = (0x10 or (cc[pid] and 0x0F)).toByte()
        cc[pid] = (cc[pid] + 1) and 0x0F
        pkt[4] = 0                                   // pointer field
        System.arraycopy(section, 0, pkt, 5, section.size)
        out.write(pkt)
    }

    private fun pat(): ByteArray = withCrc(byteArrayOf(
        0x00, 0xB0.toByte(), 13, 0x00, 0x01, 0xC1.toByte(), 0x00, 0x00,
        0x00, 0x01, (0xE0 or (pmtPid shr 8)).toByte(), (pmtPid and 0xFF).toByte()))

    private fun pmt(): ByteArray = withCrc(byteArrayOf(
        0x02, 0xB0.toByte(), 18, 0x00, 0x01, 0xC1.toByte(), 0x00, 0x00,
        (0xE0 or (videoPid shr 8)).toByte(), (videoPid and 0xFF).toByte(), 0xF0.toByte(), 0x00,
        0x1B, (0xE0 or (videoPid shr 8)).toByte(), (videoPid and 0xFF).toByte(), 0xF0.toByte(), 0x00))

    companion object {
        fun writePts(b: ByteArray, o: Int, pts: Long) {
            b[o] = (0x21 or ((pts shr 29).toInt() and 0x0E)).toByte()
            b[o + 1] = (pts shr 22).toByte()
            b[o + 2] = (((pts shr 14).toInt() and 0xFE) or 1).toByte()
            b[o + 3] = (pts shr 7).toByte()
            b[o + 4] = (((pts shl 1).toInt() and 0xFE) or 1).toByte()
        }

        fun writePcr(b: ByteArray, o: Int, base: Long) {
            b[o] = (base shr 25).toByte()
            b[o + 1] = (base shr 17).toByte()
            b[o + 2] = (base shr 9).toByte()
            b[o + 3] = (base shr 1).toByte()
            b[o + 4] = (((base and 1).toInt() shl 7) or 0x7E).toByte()
            b[o + 5] = 0
        }

        fun crc32(data: ByteArray, len: Int): Int {
            var crc = -1
            for (i in 0 until len) {
                crc = crc xor ((data[i].toInt() and 0xFF) shl 24)
                repeat(8) { crc = if (crc < 0) (crc shl 1) xor 0x04C11DB7 else crc shl 1 }
            }
            return crc
        }

        fun withCrc(s: ByteArray): ByteArray {
            val r = s.copyOf(s.size + 4)
            val c = crc32(s, s.size)
            r[s.size] = (c ushr 24).toByte(); r[s.size + 1] = (c ushr 16).toByte()
            r[s.size + 2] = (c ushr 8).toByte(); r[s.size + 3] = c.toByte()
            return r
        }
    }
}
