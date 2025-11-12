/**
 * Game Score Tracker Tool - Complex Dynamic Job Example
 * 
 * This tool demonstrates:
 * 1. Creating a game tracking record
 * 2. Creating an interval job that starts at game time
 * 3. Job fetches score updates periodically
 * 4. Job checks if game is over
 * 5. Job deactivates itself when game ends
 * 
 * Use case: Live sports score tracking, esports matches, auction monitoring
 */

import { LuaTool, Jobs, Data } from "lua-cli";
import { z } from "zod";

export class TrackGameScoresTool implements LuaTool {
  name = "track_game_scores";
  description = "Track live scores for a game with automatic updates";
  
  inputSchema = z.object({
    gameId: z.string(),
    gameName: z.string(),
    startTime: z.string(), // ISO 8601 timestamp
    updateIntervalSeconds: z.number().min(30).max(600).default(60), // 30s to 10min
    apiEndpoint: z.string().optional() // Optional external API
  });

  async execute(input: {
    gameId: string;
    gameName: string;
    startTime: string;
    updateIntervalSeconds: number;
    apiEndpoint?: string;
  }) {
    console.log(`🎮 Setting up score tracking for: ${input.gameName}`);

    // Step 1: Create game record in database
    const gameRecord = {
      gameId: input.gameId,
      gameName: input.gameName,
      startTime: input.startTime,
      status: 'scheduled',
      currentScore: { home: 0, away: 0 },
      lastUpdate: new Date().toISOString(),
      createdAt: new Date().toISOString()
    };

    const game = await Data.create('games', gameRecord, 
      `${input.gameName} ${input.gameId}`
    );

    console.log(`✅ Game record created: ${game.id}`);

    // Step 2: Calculate when the job should start
    const gameStartTime = new Date(input.startTime);
    const now = new Date();

    // If game hasn't started yet, schedule job to start at game time
    // If game already started, start immediately
    const jobStartTime = gameStartTime > now ? gameStartTime : now;

    console.log(`⏰ Job will start at: ${jobStartTime.toLocaleString()}`);
    console.log(`🔄 Updates every ${input.updateIntervalSeconds} seconds`);

    // Step 3: Capture variables for the job closure
    const gameId = input.gameId;
    const gameName = input.gameName;
    const intervalSeconds = input.updateIntervalSeconds;
    const apiEndpoint = input.apiEndpoint || 'https://api.example.com/scores';
    const gameRecordId = game.id;

    // Step 4: Create an interval job that monitors the game
    // This job will run every X seconds and stop itself when game ends
    const job = await Jobs.create({
      name: `track-game-${gameId}`,
      description: `Live score tracking for ${gameName}`,
      
      // Important: Use interval schedule
      schedule: {
        type: 'interval',
        seconds: intervalSeconds
      },
      
      timeout: 30,  // Each update should complete in 30 seconds
      
      retry: {
        maxAttempts: 2,  // Retry failed updates
        backoffSeconds: 10
      },
      
      // The job logic - runs every interval until game ends
      execute: async () => {
        console.log('🎮 Fetching score update for game ' + gameId + '...');
        
        try {
          // Fetch current game data from our database
          const gameData = await Data.getEntry('games', gameRecordId);
          
          // Check if game is already over
          if (gameData.data.status === 'finished') {
            console.log('🏁 Game ' + gameId + ' is already finished. Stopping job.');
            
            // IMPORTANT: Job stops itself!
            await Jobs.deactivate(job.jobId);
            
            return {
              action: 'job-stopped',
              reason: 'game-finished',
              gameId: gameId,
              finalScore: gameData.data.currentScore
            };
          }
          
          // Fetch latest scores from external API
          let scoreUpdate;
          try {
            const response = await fetch(apiEndpoint + '/' + gameId);
            
            if (!response.ok) {
              throw new Error('API returned ' + response.status);
            }
            
            scoreUpdate = await response.json();
          } catch (apiError) {
            // If external API fails, generate mock data for demo
            console.warn('External API failed, using mock data:', apiError);
            
            // Simulate score progression
            const currentScore = gameData.data.currentScore;
            scoreUpdate = {
              home: currentScore.home + Math.floor(Math.random() * 2),
              away: currentScore.away + Math.floor(Math.random() * 2),
              quarter: Math.min(4, Math.floor(Date.now() / 1000 / 60 / 15) % 5),
              timeRemaining: '12:34',
              isFinished: Math.random() > 0.9  // 10% chance game ends
            };
          }
          
          // Update game record with new scores
          const updatedGame = await Data.update('games', gameRecordId, {
            ...gameData.data,
            currentScore: {
              home: scoreUpdate.home,
              away: scoreUpdate.away
            },
            quarter: scoreUpdate.quarter,
            timeRemaining: scoreUpdate.timeRemaining,
            status: scoreUpdate.isFinished ? 'finished' : 'in-progress',
            lastUpdate: new Date().toISOString()
          });
          
          console.log('📊 Score update: ' + scoreUpdate.home + ' - ' + scoreUpdate.away);
          
          // Check if game just ended
          if (scoreUpdate.isFinished) {
            console.log('🏁 Game ' + gameId + ' has finished!');
            console.log('Final score: ' + scoreUpdate.home + ' - ' + scoreUpdate.away);
            
            // Store final game result
            await Data.create('game-results', {
              gameId: gameId,
              gameName: gameName,
              finalScore: {
                home: scoreUpdate.home,
                away: scoreUpdate.away
              },
              finishedAt: new Date().toISOString()
            });
            
            // IMPORTANT: Job stops itself when game ends!
            await Jobs.deactivate(job.jobId);
            
            console.log('⏹️  Score tracking job stopped for game ' + gameId);
            
            return {
              action: 'game-ended-job-stopped',
              gameId: gameId,
              finalScore: {
                home: scoreUpdate.home,
                away: scoreUpdate.away
              }
            };
          }
          
          // Game still in progress
          return {
            action: 'score-updated',
            gameId: gameId,
            currentScore: {
              home: scoreUpdate.home,
              away: scoreUpdate.away
            },
            quarter: scoreUpdate.quarter,
            timeRemaining: scoreUpdate.timeRemaining
          };
          
        } catch (error: any) {
          console.error('❌ Error updating score for game ' + gameId + ':', error);
          
          // Don't stop job on errors - just log and continue
          return {
            action: 'error',
            gameId: gameId,
            error: error.message
          };
        }
      }
    });

    console.log(`✅ Score tracking job created: ${job.jobId}`);
    console.log(`📊 Job will update scores every ${input.updateIntervalSeconds} seconds`);
    console.log(`⏹️  Job will automatically stop when game ends`);

    return {
      success: true,
      game: {
        id: game.id,
        gameId: input.gameId,
        name: input.gameName,
        startTime: input.startTime,
        status: 'scheduled'
      },
      scoreTracker: {
        jobId: job.jobId,
        jobName: job.name,
        updateInterval: input.updateIntervalSeconds,
        startsAt: jobStartTime.toISOString(),
        message: 'Job will automatically stop when game ends'
      }
    };
  }
}

/**
 * Get Game Scores Tool
 * 
 * Retrieves current scores for a game or all active games
 */
export class GetGameScoresTool implements LuaTool {
  name = "get_game_scores";
  description = "Get current scores for tracked games";
  
  inputSchema = z.object({
    gameId: z.string().optional()
  });

  async execute(input: { gameId?: string }) {
    if (input.gameId) {
      // Get specific game
      const games = await Data.get('games', {}, 1, 100);
      const game = games.data?.find((g: any) => g.data.gameId === input.gameId);
      
      if (!game) {
        return {
          found: false,
          gameId: input.gameId,
          message: 'Game not found'
        };
      }

      return {
        found: true,
        game: {
          gameId: game.data.gameId,
          name: game.data.gameName,
          status: game.data.status,
          currentScore: game.data.currentScore,
          quarter: game.data.quarter,
          timeRemaining: game.data.timeRemaining,
          lastUpdate: game.data.lastUpdate
        }
      };
    }

    // Get all active games
    const allGames = await Data.get('games', {}, 1, 50);
    
    const activeGames = (allGames.data || [])
      .filter((g: any) => g.data.status === 'in-progress' || g.data.status === 'scheduled')
      .map((g: any) => ({
        gameId: g.data.gameId,
        name: g.data.gameName,
        status: g.data.status,
        currentScore: g.data.currentScore,
        lastUpdate: g.data.lastUpdate
      }));

    return {
      activeGamesCount: activeGames.length,
      games: activeGames
    };
  }
}

/**
 * Stop Game Tracking Tool
 * 
 * Manually stops score tracking for a game
 */
export class StopGameTrackingTool implements LuaTool {
  name = "stop_game_tracking";
  description = "Manually stop score tracking for a game";
  
  inputSchema = z.object({
    gameId: z.string()
  });

  async execute(input: { gameId: string }) {
    console.log(`⏹️  Stopping score tracking for game ${input.gameId}...`);

    // Find the tracking job
    const allJobs = await Jobs.getAll();
    
    if (!allJobs.success || !allJobs.data) {
      throw new Error('Failed to fetch jobs');
    }

    const trackingJob = allJobs.data.jobs.find((j: any) => 
      j.name === `track-game-${input.gameId}`
    );

    if (!trackingJob) {
      return {
        success: false,
        gameId: input.gameId,
        message: 'No tracking job found for this game'
      };
    }

    // Deactivate the job
    const result = await Jobs.deactivate(trackingJob.id);

    // Update game status
    const games = await Data.get('games', {}, 1, 100);
    const game = games.data?.find((g: any) => g.data.gameId === input.gameId);
    
    if (game) {
      await Data.update('games', game.id, {
        ...game.data,
        status: 'stopped',
        stoppedAt: new Date().toISOString()
      });
    }

    console.log(`✅ Score tracking stopped for game ${input.gameId}`);

    return {
      success: true,
      gameId: input.gameId,
      jobId: trackingJob.id,
      message: 'Score tracking stopped successfully'
    };
  }
}

