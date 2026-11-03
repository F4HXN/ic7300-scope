package fr.f4hxn.ic7300lite

/*
 * Look « radio » de l'appli PC (look_radio.py) : boutons métal en relief, choix
 * enfoncés, champs en afficheurs verts façon LCD, curseurs à piste ambre et bouton
 * chromé, interrupteurs à levier avec voyant, cadres sombres à titre ambre.
 *
 * Ces fonctions portent le nom des composants Material qu'elles remplacent : déclarées
 * dans le paquet de l'appli, elles passent avant « import androidx.compose.material3.* »,
 * si bien que tout l'écran prend le look sans toucher aux appels.
 */

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ButtonColors
import androidx.compose.material3.SelectableChipColors
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.SliderDefaults
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

// couleurs de look_radio.py
val METAL_HAUT = Color(0xFF4A5560)
val METAL_MILIEU = Color(0xFF2C343C)
val METAL_BAS = Color(0xFF1A2026)
val CREUX = Color(0xFF151A1F)
val BORD = Color(0xFF5A6672)
val OMBRE = Color(0xFF0B0F13)
val TEXTE_METAL = Color(0xFFE0E6EB)
val TEXTE_ETEINT = Color(0xFF4F5B66)
val LCD_FOND = Color(0xFF050807)
val LCD_VERT = Color(0xFF7CFC9A)
val LCD_VERT_ETEINT = Color(0xFF2E5236)
val LCD_BORD = Color(0xFF2A3138)
val AMBRE = Color(0xFFFFB300)
val AMBRE_ETEINT = Color(0xFF6B5A2A)
val VOYANT_VERT = Color(0xFF1FAA45)
val CADRE_HAUT = Color(0xFF161C22)
val CADRE_BAS = Color(0xFF101418)

private val metal = Brush.verticalGradient(0f to METAL_HAUT, 0.5f to METAL_MILIEU, 1f to METAL_BAS)
private val enfonce = Brush.verticalGradient(listOf(CREUX, METAL_MILIEU))
private val chrome = Brush.horizontalGradient(0f to Color(0xFF8A96A2), 0.5f to Color(0xFFDFE6EC), 1f to Color(0xFF6B7682))
private val chromeEteint = Brush.horizontalGradient(listOf(Color(0xFF6B7682), Color(0xFF3A444F)))

/** Bouton métal ; [allume] : bouton coloré (PTT en émission…), [creux] : bouton enfoncé (choix actif). */
@Composable
private fun Metal(
    onClick: () -> Unit, modifier: Modifier, enabled: Boolean, interactionSource: MutableInteractionSource?,
    allume: Color?, texteAllume: Color?, creux: Boolean, content: @Composable RowScope.() -> Unit,
) {
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val presse by source.collectIsPressedAsState()
    val forme = RoundedCornerShape(5.dp)
    val fond = when {
        allume != null -> Brush.verticalGradient(listOf(allume, allume.copy(alpha = 0.75f)))
        presse || creux -> enfonce
        else -> metal
    }
    val couleur = when {
        !enabled -> TEXTE_ETEINT
        allume != null -> texteAllume ?: Color.Black
        creux -> LCD_VERT
        else -> TEXTE_METAL
    }
    Row(
        modifier
            .defaultMinSize(minHeight = 44.dp)
            .shadow(if (presse || creux) 0.dp else 2.dp, forme)
            .clip(forme)
            .background(fond)
            .border(1.dp, if (presse || creux) OMBRE else BORD, forme)
            .clickable(interactionSource = source, indication = null, enabled = enabled, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CompositionLocalProvider(
            LocalContentColor provides couleur,
            LocalTextStyle provides LocalTextStyle.current.merge(TextStyle(color = couleur, fontWeight = FontWeight.Bold)),
        ) { content() }
    }
}

@Composable
fun Button(
    onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true,
    interactionSource: MutableInteractionSource? = null, colors: ButtonColors? = null,
    content: @Composable RowScope.() -> Unit,
) = Metal(onClick, modifier, enabled, interactionSource,
    allume = colors?.let { if (enabled) it.containerColor else null }, texteAllume = colors?.contentColor,
    creux = false, content = content)

@Composable
fun OutlinedButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable RowScope.() -> Unit) =
    Metal(onClick, modifier, enabled, null, null, null, false, content)

/** Choix (largeur, pas, bande, mode, VFO, ATU) : bouton métal, enfoncé et vert quand il est choisi. */
@Composable
fun FilterChip(
    selected: Boolean, onClick: () -> Unit, label: @Composable () -> Unit, modifier: Modifier = Modifier,
    enabled: Boolean = true, @Suppress("UNUSED_PARAMETER") colors: SelectableChipColors? = null,
) = Metal(onClick, modifier, enabled, null, null, null, creux = selected) { label() }

/** Cadre sombre, comme les QGroupBox du PC. */
@Composable
fun Card(modifier: Modifier = Modifier, shape: Shape = RoundedCornerShape(6.dp), content: @Composable ColumnScope.() -> Unit) {
    val forme = RoundedCornerShape(6.dp)
    Column(modifier.clip(forme).background(Brush.verticalGradient(listOf(CADRE_HAUT, CADRE_BAS))).border(1.dp, LCD_BORD, forme),
        content = content)
}

/** Curseur à piste ambre et bouton chromé. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Slider(
    value: Float, onValueChange: (Float) -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true,
    valueRange: ClosedFloatingPointRange<Float> = 0f..1f, onValueChangeFinished: (() -> Unit)? = null,
) {
    val couleurs = SliderDefaults.colors(
        activeTrackColor = AMBRE, inactiveTrackColor = LCD_FOND,
        disabledActiveTrackColor = AMBRE_ETEINT, disabledInactiveTrackColor = LCD_FOND,
    )
    androidx.compose.material3.Slider(
        value = value, onValueChange = onValueChange, modifier = modifier, enabled = enabled,
        valueRange = valueRange, onValueChangeFinished = onValueChangeFinished, colors = couleurs,
        thumb = {
            Box(Modifier.size(18.dp, 30.dp).shadow(2.dp, RoundedCornerShape(5.dp)).clip(RoundedCornerShape(5.dp))
                .background(if (enabled) chrome else chromeEteint).border(1.dp, OMBRE, RoundedCornerShape(5.dp)))
        },
        track = { etat ->
            SliderDefaults.Track(sliderState = etat, modifier = Modifier.height(6.dp).border(1.dp, LCD_BORD, RoundedCornerShape(3.dp)),
                colors = couleurs, enabled = enabled, thumbTrackGapSize = 0.dp, drawStopIndicator = null)
        },
    )
}

/** Interrupteur à levier avec voyant vert, comme les cases du PC. */
@Composable
fun Switch(checked: Boolean, onCheckedChange: (Boolean) -> Unit, enabled: Boolean = true) {
    Row(
        Modifier.clip(RoundedCornerShape(14.dp)).clickable(enabled = enabled) { onCheckedChange(!checked) }.padding(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // voyant
        Box(Modifier.size(12.dp).clip(CircleShape).background(
            if (checked && enabled) Brush.radialGradient(listOf(Color(0xFFD0FFD8), VOYANT_VERT))
            else Brush.radialGradient(listOf(BORD, METAL_BAS))).border(1.dp, OMBRE, CircleShape))
        Spacer(Modifier.width(6.dp))
        // levier : piste sombre et bouton chromé à gauche ou à droite
        Box(Modifier.size(44.dp, 22.dp).clip(RoundedCornerShape(11.dp)).background(LCD_FOND).border(1.dp, LCD_BORD, RoundedCornerShape(11.dp)),
            contentAlignment = if (checked) Alignment.CenterEnd else Alignment.CenterStart) {
            Box(Modifier.padding(2.dp).size(18.dp).clip(CircleShape).background(if (enabled) chrome else chromeEteint).border(1.dp, OMBRE, CircleShape))
        }
    }
}

/** Champ de saisie en afficheur vert. */
@Composable
fun OutlinedTextField(
    value: String, onValueChange: (String) -> Unit, modifier: Modifier = Modifier,
    label: (@Composable () -> Unit)? = null, singleLine: Boolean = false, isError: Boolean = false,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
) = androidx.compose.material3.OutlinedTextField(
    value, onValueChange, modifier, label = label, singleLine = singleLine, isError = isError, keyboardOptions = keyboardOptions,
    textStyle = LocalTextStyle.current.merge(TextStyle(color = LCD_VERT, fontWeight = FontWeight.Bold)),
    colors = OutlinedTextFieldDefaults.colors(
        focusedContainerColor = LCD_FOND, unfocusedContainerColor = LCD_FOND, disabledContainerColor = LCD_FOND,
        focusedBorderColor = AMBRE, unfocusedBorderColor = LCD_BORD, cursorColor = LCD_VERT,
        focusedLabelColor = AMBRE, unfocusedLabelColor = Color(0xFF8FA0B0),
        focusedTextColor = LCD_VERT, unfocusedTextColor = LCD_VERT, disabledTextColor = LCD_VERT_ETEINT,
    ),
)
