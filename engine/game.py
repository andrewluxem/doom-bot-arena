"""ViZDoom setup shared by the runner and the benches."""

import vizdoom as vzd

MIN_VERSION = (1, 3, 0)  # first release with the notifications buffer
TICRATE = 35
SKILL = 3  # decides which map things spawn; map.json uses the same value
WIDTH, HEIGHT = 320, 240

VARIABLES = [
    "PLAYER_NUMBER", "FRAGCOUNT", "DEATHCOUNT", "DEAD", "HEALTH", "ARMOR",
    "POSITION_X", "POSITION_Y", "ANGLE", "SELECTED_WEAPON",
] + [f"PLAYER{i}_FRAGCOUNT" for i in range(1, 9)] + [
    "POSITION_Z", "PITCH", "SELECTED_WEAPON_AMMO",
] + [f"WEAPON{i}" for i in range(10)] + [f"AMMO{i}" for i in range(10)] + [
    "HITCOUNT", "HITS_TAKEN", "DAMAGECOUNT", "DAMAGE_TAKEN",
]
# WEAPONn is the number of weapons owned in slot n (2 in slot 3 with both shotguns); AMMOn is the ammo
# of slot n's weapons. Probed on 1.3.2.

BUTTONS = [
    "ATTACK", "USE", "MOVE_FORWARD", "MOVE_BACKWARD", "MOVE_LEFT", "MOVE_RIGHT",
    "TURN_LEFT_RIGHT_DELTA", "LOOK_UP_DOWN_DELTA", "SPEED",
]

# Recorded in match.json. Message rendering stays off so obituary text never reaches POV video; the
# deathmatch scoreboard (with ViZDoom's "Respawn in" line) and the mid-screen "A secret is revealed!"
# are off so frames show only the world and the weapon.
RENDER = {"hud": False, "crosshair": False, "weapon": True, "messages": False, "scoreboard": False,
          "secret_message": False}


def version():
    return vzd.__version__


def version_tuple(v=None):
    parts = []
    for p in (v or vzd.__version__).split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def check_version():
    """Return an error message if this ViZDoom cannot capture events, else None."""
    v = vzd.__version__
    if version_tuple(v) < MIN_VERSION or not hasattr(vzd.DoomGame, "set_notifications_buffer_enabled"):
        return (f"ViZDoom {v} has no notifications buffer, so obituaries (and events.jsonl) cannot be "
                f"captured. Bundles need ViZDoom >= {'.'.join(map(str, MIN_VERSION))}; match runs use the "
                f"Linux pin in requirements-linux.txt. 1.2.4 is for development only.")
    return None


def host_args(nplayers, port, respawn_delay, cheats=False, spawnfarthest=True):
    args = [
        f"-host {nplayers}", f"-port {port}", "-netmode 0", "-deathmatch", "-nomonsters",
        "+sv_forcerespawn 1", "+sv_noautoaim 1", "+sv_respawnprotect 1",
        f"+sv_spawnfarthest {1 if spawnfarthest else 0}", "+sv_nocrouch 1", "+sv_noexit 1",
        "+viz_nocheat 0", f"+viz_respawn_delay {respawn_delay}",
    ]
    if cheats:
        args.append("+sv_cheats 1")
    return args


def client_args(port):
    return ["-join 127.0.0.1", f"-port {port}"]


def player_args(name, colorset, scoreboard=False):
    """neverswitchonpickup keeps the weapon the controller selected: the engine never swaps it on pickup.
    sb_deathmatch_enable gates ZDoom's dead-player scoreboard and its "Respawn in" line (hu_scores.cpp).
    cl_showsecretmessage gates the mid-screen secret message, which set_render_messages does not."""
    return [f"+name {name}", f"+colorset {colorset}", "+cl_bbannounce 0", "+show_obituaries 1",
            "+neverswitchonpickup 1", f"+sb_deathmatch_enable {1 if scoreboard else 0}",
            f"+cl_showsecretmessage {1 if RENDER['secret_message'] else 0}"]


def make_game(*, iwad, map_name, seed, args, wads, render_messages=False):
    g = vzd.DoomGame()
    g.set_doom_game_path(iwad)
    g.set_doom_map(map_name)
    g.set_mode(vzd.Mode.PLAYER)
    g.set_seed(seed)
    g.set_doom_skill(SKILL)
    g.set_episode_timeout(0)
    g.set_window_visible(False)
    g.set_sound_enabled(False)
    g.set_screen_format(vzd.ScreenFormat.RGB24)  # before init: frames are (240, 320, 3)
    g.set_screen_resolution(vzd.ScreenResolution.RES_320X240)
    g.set_render_hud(RENDER["hud"])
    g.set_render_crosshair(RENDER["crosshair"])
    g.set_render_weapon(RENDER["weapon"])
    g.set_render_messages(render_messages)
    g.set_labels_buffer_enabled(True)
    g.set_objects_info_enabled(True)  # only for the player's own actor (its engine sector); never for enemies
    g.set_sectors_info_enabled(True)
    g.set_notifications_buffer_enabled(True)
    g.set_notifications_buffer_size(1)  # exactly the lines printed during the tic just simulated
    g.set_available_buttons([getattr(vzd.Button, b) for b in BUTTONS])
    g.set_available_game_variables([getattr(vzd.GameVariable, v) for v in VARIABLES])
    for a in args:
        g.add_game_args(a)
    for w in wads:
        g.add_game_args(f"-file {w}")
    return g


def action_vector(action):
    """Controller actions are {button: value}; missing buttons are 0."""
    unknown = set(action) - set(BUTTONS)
    if unknown:
        raise ValueError(f"unknown buttons {sorted(unknown)}")
    return [float(action.get(b, 0)) for b in BUTTONS]
