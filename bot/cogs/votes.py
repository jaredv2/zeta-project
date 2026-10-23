"""Timed lobby-background submissions, reaction voting, and staff review."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from checks import admin_only, is_admin_member
from db import (
    KeyStore,
    SubmissionLimitReached,
    VoteAlreadyActive,
    VoteSubmissionsPending,
    VoteUnavailable,
)
from ui import COLOR_BRAND, COLOR_OK, simple_embed

if TYPE_CHECKING:
    from main import ZetaBot

log = logging.getLogger("zeta.votes")

VOTE_EMOJI = "✅"
PRIVILEGED_SUBMITTER_ROLE_ID = 1554961776255442956
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DURATIONS = {
    "1h": dt.timedelta(hours=1),
    "1d": dt.timedelta(days=1),
    "3d": dt.timedelta(days=3),
    "1w": dt.timedelta(weeks=1),
    "3w": dt.timedelta(weeks=3),
    "1m": dt.timedelta(days=30),
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def is_image(attachment: discord.Attachment) -> bool:
    suffix = "." + attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else ""
    return suffix in IMAGE_EXTENSIONS


def vote_count(message: discord.Message) -> int:
    return sum(
        max(0, reaction.count - (1 if reaction.me else 0))
        for reaction in message.reactions
        if str(reaction.emoji) == VOTE_EMOJI
    )


class VoteReviewView(discord.ui.View):
    """Persistent staff decision buttons for one submission."""

    def __init__(self, cog: "Votes", submission_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.submission_id = submission_id
        approve = discord.ui.Button(
            label="Select background",
            style=discord.ButtonStyle.success,
            custom_id=f"zeta:votes:approve:{submission_id}",
        )
        deny = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"zeta:votes:reject:{submission_id}",
        )
        approve.callback = self.approve
        deny.callback = self.reject
        self.add_item(approve)
        self.add_item(deny)

    async def _decide(self, interaction: discord.Interaction, approved: bool) -> None:
        member = interaction.user
        if (
            interaction.guild is None
            or not isinstance(member, discord.Member)
            or not is_admin_member(member, self.cog.bot.settings)
        ):
            await interaction.response.send_message(
                "Only configured server staff can review lobby backgrounds.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        submission = await self.cog.store.resolve_vote_submission(
            self.submission_id, approved=approved, actor_id=member.id
        )
        if submission is None:
            await interaction.followup.send("This submission has already been reviewed.", ephemeral=True)
            return

        if interaction.message is not None:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException as exc:
                log.warning("Could not disable reviewed message %s: %s", interaction.message.id, exc)

        if approved:
            try:
                await self.cog.announce_selection(submission)
            except discord.HTTPException:
                log.exception("Submission %s was selected but its announcement failed", self.submission_id)
                await interaction.followup.send(
                    f"Submission #{self.submission_id} was selected, but I couldn't post the announcement. "
                    "Please check the submission channel permissions.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"Submission #{self.submission_id} was selected and announced.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Submission #{self.submission_id} was rejected. The next-ranked entry is being reviewed.",
                ephemeral=True,
            )
            await self.cog.post_next_review(submission["vote_id"])

    async def approve(self, interaction: discord.Interaction) -> None:
        await self._decide(interaction, True)

    async def reject(self, interaction: discord.Interaction) -> None:
        await self._decide(interaction, False)


class Votes(commands.Cog):
    def __init__(self, bot: "ZetaBot") -> None:
        self.bot = bot
        self._close_tasks: dict[int, asyncio.Task[None]] = {}
        self._review_retry_tasks: dict[int, asyncio.Task[None]] = {}
        self._resume_task: Optional[asyncio.Task[None]] = None
        self._resumed = False

    @property
    def store(self) -> KeyStore:
        assert self.bot.store is not None, "store not ready"
        return self.bot.store

    def cog_unload(self) -> None:
        if self._resume_task is not None:
            self._resume_task.cancel()
        for task in self._close_tasks.values():
            task.cancel()
        self._close_tasks.clear()
        for task in self._review_retry_tasks.values():
            task.cancel()
        self._review_retry_tasks.clear()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._resumed:
            self._resumed = True
            self._resume_task = asyncio.create_task(self._resume_state(), name="zeta-votes-resume")

    async def _resume_state(self) -> None:
        try:
            for vote in await self.store.get_open_votes():
                self._schedule_close(vote["id"], vote["closes_at"])
            for vote in await self.store.get_votes_awaiting_review():
                await self.post_next_review(vote["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not restore lobby vote state after startup")

    def _schedule_close(self, vote_id: int, closes_at: dt.datetime) -> None:
        old_task = self._close_tasks.get(vote_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()
        self._close_tasks[vote_id] = asyncio.create_task(
            self._close_after(vote_id, closes_at), name=f"zeta-vote-close-{vote_id}"
        )

    async def _close_after(self, vote_id: int, closes_at: dt.datetime) -> None:
        retry = False
        try:
            delay = max(0.0, (closes_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
            await asyncio.sleep(delay)
            while True:
                submissions = await self.store.get_vote_submissions_for_tally(vote_id)
                counts: dict[int, int] = {}
                retry_tally = False
                for submission in submissions:
                    if submission["review_status"] == "posting":
                        continue
                    try:
                        channel = await self._fetch_text_channel(submission["submission_channel_id"])
                        message = await channel.fetch_message(submission["message_id"])
                        counts[submission["id"]] = vote_count(message)
                    except discord.NotFound:
                        log.warning(
                            "Submission message %s was deleted before vote %s closed",
                            submission["message_id"], vote_id,
                        )
                        counts[submission["id"]] = 0
                    except discord.HTTPException:
                        log.exception(
                            "Could not fetch submission message %s while closing vote %s",
                            submission["message_id"], vote_id,
                        )
                        retry_tally = True
                        break
                if retry_tally:
                    await asyncio.sleep(30)
                    continue
                try:
                    await self.store.finish_vote_tally(vote_id, counts)
                    break
                except VoteSubmissionsPending:
                    await asyncio.sleep(5)
            await self.post_next_review(vote_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not close lobby background vote %s", vote_id)
            retry = True
        finally:
            if self._close_tasks.get(vote_id) is asyncio.current_task():
                self._close_tasks.pop(vote_id, None)
                if retry:
                    self._close_tasks[vote_id] = asyncio.create_task(
                        self._retry_close(vote_id), name=f"zeta-vote-retry-{vote_id}"
                    )

    async def _retry_close(self, vote_id: int) -> None:
        await asyncio.sleep(30)
        if self._close_tasks.get(vote_id) is asyncio.current_task():
            self._close_tasks.pop(vote_id, None)
            self._schedule_close(vote_id, dt.datetime.now(dt.timezone.utc))

    async def _fetch_text_channel(self, channel_id: int) -> discord.TextChannel:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Vote channel {channel_id} is not a text channel")
        return channel

    async def post_next_review(self, vote_id: int) -> None:
        try:
            submission = await self.store.claim_next_vote_review(vote_id)
            if submission is None:
                return
            if submission["review_message_id"] is not None:
                self.bot.add_view(
                    VoteReviewView(self, submission["id"]),
                    message_id=submission["review_message_id"],
                )
                return

            config = await self.store.get_vote_config(submission["guild_id"])
            if config is None:
                log.error("No review channel configured for vote %s", vote_id)
                self._schedule_review_retry(vote_id)
                return
            channel = await self._fetch_text_channel(config["review_channel_id"])
            embed = discord.Embed(
                title=f"Lobby background submission #{submission['id']}",
                description=(
                    f"Submitted by <@{submission['submitter_id']}>\n"
                    f"Votes: **{submission['vote_count']}**"
                ),
                color=COLOR_BRAND,
            )
            if submission["image_url"]:
                embed.set_image(url=submission["image_url"])
            message = await channel.send(
                embed=embed,
                view=VoteReviewView(self, submission["id"]),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self.store.set_vote_review_message(submission["id"], message.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not post the next submission for vote %s review", vote_id)
            self._schedule_review_retry(vote_id)

    def _schedule_review_retry(self, vote_id: int) -> None:
        existing = self._review_retry_tasks.get(vote_id)
        if existing is None or existing.done():
            self._review_retry_tasks[vote_id] = asyncio.create_task(
                self._retry_review(vote_id), name=f"zeta-vote-review-retry-{vote_id}"
            )

    async def _retry_review(self, vote_id: int) -> None:
        try:
            await asyncio.sleep(60)
            if self._review_retry_tasks.get(vote_id) is asyncio.current_task():
                self._review_retry_tasks.pop(vote_id, None)
                await self.post_next_review(vote_id)
        except asyncio.CancelledError:
            raise

    async def announce_selection(self, submission) -> None:
        channel = await self._fetch_text_channel(submission["submission_channel_id"])
        embed = discord.Embed(
            title="Selected lobby background",
            color=COLOR_OK,
        )
        if submission["image_url"]:
            embed.set_image(url=submission["image_url"])
        await channel.send(
            content=f"Next lobby background submitted by <@{submission['submitter_id']}> was selected!",
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="set-review-channel", description="Choose where lobby backgrounds are reviewed")
    @app_commands.describe(channel="Staff-only channel used to approve or reject submissions")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only()
    async def set_review_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        me = guild.me
        if me is None:
            await interaction.response.send_message("I couldn't load my server permissions.", ephemeral=True)
            return
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"I need View Channel, Send Messages, and Embed Links in {channel.mention}.",
                ephemeral=True,
            )
            return
        await self.store.set_vote_review_channel(guild.id, channel.id)
        await interaction.response.send_message(
            embed=simple_embed(
                "Review channel saved",
                f"Submissions will be reviewed in {channel.mention}.",
                COLOR_OK,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="open-votes", description="Open timed lobby-background submissions and voting")
    @app_commands.describe(
        channel="Channel where members submit images and vote",
        max_time="How long submissions and voting stay open",
    )
    @app_commands.choices(
        max_time=[
            app_commands.Choice(name="1 hour", value="1h"),
            app_commands.Choice(name="1 day", value="1d"),
            app_commands.Choice(name="3 days", value="3d"),
            app_commands.Choice(name="1 week", value="1w"),
            app_commands.Choice(name="3 weeks", value="3w"),
            app_commands.Choice(name="1 month (30 days)", value="1m"),
        ]
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only()
    async def open_votes(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        max_time: app_commands.Choice[str],
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        config = await self.store.get_vote_config(guild.id)
        if config is None:
            await interaction.response.send_message(
                "Configure a staff review channel first with `/set-review-channel`.",
                ephemeral=True,
            )
            return
        me = guild.me
        perms = channel.permissions_for(me) if me else None
        if perms is None or not (
            perms.view_channel and perms.send_messages and perms.embed_links
            and perms.read_message_history and perms.attach_files and perms.add_reactions
        ):
            await interaction.response.send_message(
                f"I need View Channel, Read Message History, Send Messages, Embed Links, Attach Files, "
                f"and Add Reactions "
                f"in {channel.mention}.",
                ephemeral=True,
            )
            return
        try:
            vote = await self.store.open_vote(
                guild_id=guild.id,
                channel_id=channel.id,
                actor_id=interaction.user.id,
                duration=DURATIONS[max_time.value],
            )
        except VoteAlreadyActive as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self._schedule_close(vote["id"], vote["closes_at"])
        try:
            await channel.send(
                f"Lobby background voting is open until <t:{int(vote['closes_at'].timestamp())}:F>.\n"
                f"Submit an image with `/add-sub`; react with {VOTE_EMOJI} to vote."
            )
        except discord.HTTPException as exc:
            log.exception("Vote %s opened but its announcement failed", vote["id"])
            await interaction.response.send_message(
                f"Vote #{vote['id']} is open, but I couldn't post its announcement: {exc}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=simple_embed(
                "Lobby background vote opened",
                f"Submissions and voting are open in {channel.mention} for {max_time.name}. "
                f"Review will begin automatically when it closes.",
                COLOR_OK,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="add-sub", description="Submit an image to the active lobby-background vote")
    @app_commands.describe(image="A lobby-background image (PNG, JPG, GIF, or WebP; max 10 MB)")
    @app_commands.guild_only()
    async def add_sub(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        guild = interaction.guild
        assert guild is not None
        if not is_image(image) or image.size > MAX_IMAGE_BYTES:
            await interaction.response.send_message(
                "Upload a PNG, JPG, GIF, or WebP image no larger than 10 MB.",
                ephemeral=True,
            )
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Submit this command in the active vote channel.", ephemeral=True
            )
            return
        active_vote = await self.store.get_active_vote(guild.id)
        if active_vote is None or active_vote["submission_channel_id"] != channel.id:
            await interaction.response.send_message(
                "There is no active lobby-background vote in this channel.", ephemeral=True
            )
            return

        member = interaction.user
        privileged = isinstance(member, discord.Member) and any(
            role.id == PRIVILEGED_SUBMITTER_ROLE_ID or role.name.lower() == "access"
            for role in member.roles
        )
        submission_limit = 2 if privileged else 1
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            reservation = await self.store.reserve_vote_submission(
                guild_id=guild.id,
                submitter_id=member.id,
                submission_limit=submission_limit,
            )
        except (VoteUnavailable, SubmissionLimitReached) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Lobby background submission #{reservation['id']}",
            color=COLOR_BRAND,
        )
        embed.set_image(url=f"attachment://{image.filename}")
        try:
            posted = await channel.send(
                content=f"Submitted by {member.mention}\nSubmission ID: `#{reservation['id']}`",
                embed=embed,
                file=await image.to_file(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if not posted.attachments:
                raise RuntimeError("Discord did not return the uploaded image attachment")
            await self.store.publish_vote_submission(
                reservation["id"],
                message_id=posted.id,
                image_url=posted.attachments[0].url,
                file_name=posted.attachments[0].filename,
            )
        except (discord.HTTPException, RuntimeError):
            log.exception("Could not publish lobby background submission %s", reservation["id"])
            await self.store.delete_vote_submission_reservation(reservation["id"])
            await interaction.followup.send(
                "I couldn't publish that image. Check the bot's channel permissions and try again.",
                ephemeral=True,
            )
            return
        except Exception:
            await self.store.delete_vote_submission_reservation(reservation["id"])
            raise

        try:
            await posted.add_reaction(VOTE_EMOJI)
        except discord.HTTPException:
            log.exception("Submission %s posted but the bot could not add the vote reaction", reservation["id"])
            await interaction.followup.send(
                f"Your image was submitted as **#{reservation['id']}**, but I couldn't add the vote reaction. "
                "Please check the bot's Add Reactions permission.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Your image was submitted as **#{reservation['id']}**. "
            f"React with {VOTE_EMOJI} to vote for entries.",
            ephemeral=True,
        )


async def setup(bot: "ZetaBot") -> None:
    await bot.add_cog(Votes(bot))
